"""Command-line entry point.

Subcommands:
  * `doctor`               — passive diagnosis (default).
  * `doctor why <symptom>` — active diagnosis biased toward the symptom.
  * `doctor feedback`      — record outcome of the most recent diagnosis.

Both diagnosis modes share collectors and analyzers; they differ in the
optional symptom passed to the ranker and the LLM. `--no-ai` skips the
LLM call entirely and shows the deterministic output. Every diagnosis
run also retrieves on-demand RAG snippets (changelogs, AppArmor
profiles, apport reports) and similar past incidents from the local
feedback store, feeding both into the LLM prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.apparmor_denials import ANALYZER as AAD
from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.analyzers.cache_health import ANALYZER as CH
from ubuntu_doctor.analyzers.firmware_mismatch import ANALYZER as FWM
from ubuntu_doctor.analyzers.held_packages import ANALYZER as HP
from ubuntu_doctor.analyzers.irq_driver_regression import ANALYZER as IRQ
from ubuntu_doctor.analyzers.oom_attribution import ANALYZER as OOMA
from ubuntu_doctor.analyzers.postupgrade_regression import ANALYZER as PUR
from ubuntu_doctor.analyzers.snap_refresh_breakage import ANALYZER as SRB
from ubuntu_doctor.analyzers.systemd_health import ANALYZER as SYSH
from ubuntu_doctor.collectors.apparmor_audit import COLLECTOR as AAUD
from ubuntu_doctor.collectors.apt_log import COLLECTOR as APTL
from ubuntu_doctor.collectors.cache_state import COLLECTOR as CS
from ubuntu_doctor.collectors.dmesg import COLLECTOR as DMSG
from ubuntu_doctor.collectors.diskspace import COLLECTOR as DS
from ubuntu_doctor.collectors.dpkg_history import COLLECTOR as DPKG
from ubuntu_doctor.collectors.hardware import COLLECTOR as HW
from ubuntu_doctor.collectors.journald import COLLECTOR as JRND
from ubuntu_doctor.collectors.snap_changes import COLLECTOR as SNAP
from ubuntu_doctor.collectors.systemd_failed import COLLECTOR as SYSD
from ubuntu_doctor.feedback import (
    IncidentStore,
    LastRunCache,
    compute_fingerprint,
)
from ubuntu_doctor.feedback.lastrun import LastRun, LastRunHypothesis, now_iso
from ubuntu_doctor.feedback.recorder import record_feedback
from ubuntu_doctor.feedback.store import Incident
from ubuntu_doctor.llm import LLMClient, LLMExplanation, LLMUnavailable
from ubuntu_doctor.llm.client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
)
from ubuntu_doctor.orchestrator import build_snapshot, run_analyzers
from ubuntu_doctor.rag import RetrievedSnippet, retrieve_for_hypotheses
from ubuntu_doctor.ranker import rank as rank_by_symptom
from ubuntu_doctor.snapshot import Hypothesis, Snapshot
from ubuntu_doctor.ui import jsonout, text

COLLECTORS = (DPKG, SYSD, DMSG, JRND, APTL, SNAP, AAUD, HW, DS, CS)
# Order matters only for output stability — analyzers run concurrently
# under the orchestrator. Keyed by analyzer id so the CLI can selectively
# enable/disable individual analyzers by name.
ANALYZER_REGISTRY: dict[str, Analyzer] = {
    a.id: a for a in (PUR, SYSH, AAD, HP, CH, OOMA, FWM, SRB, IRQ)
}

_SINCE_PATTERN = re.compile(r"^(\d+)([smhdw])$")
_SINCE_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_since(value: str) -> timedelta:
    match = _SINCE_PATTERN.match(value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(
            f"--since must look like '14d', '6h', '30m' — got {value!r}"
        )
    amount, unit = int(match.group(1)), match.group(2)
    return timedelta(**{_SINCE_UNITS[unit]: amount})


def parse_analyzer_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def select_analyzers(
    enabled: list[str] | None,
    skipped: list[str] | None,
) -> tuple[Analyzer, ...]:
    """Apply --analyzers (allowlist) then --skip-analyzers (denylist).

    Unknown ids on either side are a hard error: the user almost
    certainly typo'd, and silently running everything would mask that.
    """
    known = set(ANALYZER_REGISTRY)
    requested = set(enabled) if enabled is not None else set(known)
    skipped_set = set(skipped or ())
    unknown = (requested | skipped_set) - known
    if unknown:
        raise SystemExit(
            "unknown analyzer(s): "
            f"{', '.join(sorted(unknown))}. "
            f"Available: {', '.join(sorted(known))}."
        )
    selected_ids = requested - skipped_set
    return tuple(
        analyzer
        for aid, analyzer in ANALYZER_REGISTRY.items()
        if aid in selected_ids
    )


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--since",
        type=parse_since,
        default=timedelta(days=14),
        help="Time window to analyse (e.g. 14d, 6h, 30m). Default: 14d.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip the LLM call; deterministic analyzers only.",
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="Skip the RAG retrieval step (changelogs, AppArmor profiles, "
        "apport reports). Useful if filesystem reads are slow.",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Don't consult or write to the local feedback store.",
    )
    analyzer_ids = ", ".join(sorted(ANALYZER_REGISTRY))
    parser.add_argument(
        "--analyzers",
        type=parse_analyzer_list,
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated allowlist of analyzers to run. "
            f"Default: all. Available: {analyzer_ids}."
        ),
    )
    parser.add_argument(
        "--skip-analyzers",
        type=parse_analyzer_list,
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated list of analyzers to skip. Applied after "
            "--analyzers."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LLM model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            f"OpenAI-compatible base URL of the local Inference Snap "
            f"(default: {DEFAULT_BASE_URL})."
        ),
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            f"Seconds to wait for the LLM (default: "
            f"{DEFAULT_TIMEOUT_SECONDS})."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doctor",
        description="Diagnose Ubuntu system problems with a local LLM.",
    )
    _add_common_options(parser)
    subparsers = parser.add_subparsers(dest="cmd")

    sp_why = subparsers.add_parser(
        "why",
        help="Active diagnosis: explain why a specific symptom is happening.",
        description=(
            "Active diagnosis. Pass a symptom phrase (e.g. "
            "'doctor why audio stopped working'); ubuntu-doctor will "
            "bias the ranking and the LLM prompt toward that symptom."
        ),
    )
    _add_common_options(sp_why)
    sp_why.add_argument(
        "symptom",
        nargs="+",
        help="A short phrase describing the symptom you're investigating.",
    )

    subparsers.add_parser(
        "feedback",
        help="Record outcome of the most recent diagnosis.",
        description=(
            "Interactive: prompts for which hypothesis was the cause, "
            "what you ran, and what happened. Saves to the local "
            "feedback store so future runs can use it as a few-shot "
            "example."
        ),
    )

    return parser


def _maybe_llm_client(args: argparse.Namespace) -> LLMClient | None:
    if args.no_ai:
        return None
    return LLMClient(
        base_url=args.base_url,
        model=args.model,
        timeout=args.llm_timeout,
    )


async def _retrieve(
    args: argparse.Namespace, hypotheses: list[Hypothesis]
) -> list[RetrievedSnippet]:
    if args.no_rag or not hypotheses:
        return []
    try:
        return await retrieve_for_hypotheses(hypotheses)
    except Exception as exc:  # noqa: BLE001
        # RAG failures are non-fatal; log to stderr and continue.
        print(f"warning: RAG retrieval failed: {exc}", file=sys.stderr)
        return []


def _past_incidents(
    args: argparse.Namespace, fingerprint: list[str]
) -> tuple[IncidentStore | None, list[Incident]]:
    if args.no_history:
        return None, []
    try:
        store = IncidentStore()
    except Exception as exc:  # noqa: BLE001
        print(f"warning: feedback store unavailable: {exc}", file=sys.stderr)
        return None, []
    try:
        return store, store.find_similar(fingerprint)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: feedback lookup failed: {exc}", file=sys.stderr)
        return store, []


async def _explain(
    client: LLMClient | None,
    snapshot: Snapshot,
    hypotheses: list[Hypothesis],
    symptom: str | None,
    retrieved: list[RetrievedSnippet],
    past_incidents: list[Incident],
) -> tuple[LLMExplanation | None, str | None]:
    if client is None:
        return None, None
    try:
        explanation = await client.explain(
            snapshot,
            hypotheses,
            symptom,
            retrieved=retrieved,
            past_incidents=past_incidents,
        )
        return explanation, None
    except LLMUnavailable as exc:
        return None, str(exc)


def _write_last_run(
    args: argparse.Namespace,
    hypotheses: list[Hypothesis],
    symptom: str | None,
    fingerprint: list[str],
) -> None:
    if args.no_history or args.json:
        # When --json is in use, the consumer is a pipeline, not an
        # interactive user — they'll feed `feedback` programmatically
        # if they want to.
        return
    try:
        cache = LastRunCache()
        cache.write(
            LastRun(
                ts=now_iso(),
                symptom=symptom,
                fingerprint=fingerprint,
                hypotheses=[
                    LastRunHypothesis(
                        id=h.id,
                        title=h.title,
                        analyzer=h.analyzer,
                        fix_commands=list(h.fix_commands),
                    )
                    for h in hypotheses[:5]
                ],
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not write last-run cache: {exc}", file=sys.stderr)


async def _run_diagnose(args: argparse.Namespace) -> int:
    symptom = (
        " ".join(args.symptom) if getattr(args, "symptom", None) else None
    )
    now = datetime.now(timezone.utc)
    window_start = now - args.since

    analyzers = select_analyzers(args.analyzers, args.skip_analyzers)
    snapshot = await build_snapshot(COLLECTORS, window_start, now)
    hypotheses = await run_analyzers(analyzers, snapshot)
    hypotheses = rank_by_symptom(hypotheses, symptom)

    fingerprint = compute_fingerprint(hypotheses)
    retrieved = await _retrieve(args, hypotheses)
    _, past = _past_incidents(args, fingerprint)

    client = _maybe_llm_client(args)
    explanation, llm_error = await _explain(
        client, snapshot, hypotheses, symptom, retrieved, past
    )

    renderer = jsonout.render if args.json else text.render
    print(
        renderer(
            snapshot,
            hypotheses,
            explanation=explanation,
            symptom=symptom,
            llm_error=llm_error,
            retrieved=retrieved,
            past_incidents=past,
        )
    )

    _write_last_run(args, hypotheses, symptom, fingerprint)
    return 0


def _run_feedback(_args: argparse.Namespace) -> int:
    code, _ = record_feedback()
    return code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "feedback":
        return _run_feedback(args)
    try:
        return asyncio.run(_run_diagnose(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
