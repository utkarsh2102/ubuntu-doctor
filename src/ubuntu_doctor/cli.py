"""Command-line entry point.

Two modes:
  * `doctor`               — passive diagnosis (default).
  * `doctor why <symptom>` — active diagnosis biased toward the symptom.

Both modes share the same collectors and analyzers; the difference is
the optional symptom passed to the ranker and the LLM. `--no-ai` skips
the LLM call entirely and shows the deterministic output.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.apparmor_denials import ANALYZER as AAD
from ubuntu_doctor.analyzers.postupgrade_regression import ANALYZER as PUR
from ubuntu_doctor.analyzers.systemd_health import ANALYZER as SYSH
from ubuntu_doctor.collectors.dmesg import COLLECTOR as DMSG
from ubuntu_doctor.collectors.dpkg_history import COLLECTOR as DPKG
from ubuntu_doctor.collectors.journald import COLLECTOR as JRND
from ubuntu_doctor.collectors.systemd_failed import COLLECTOR as SYSD
from ubuntu_doctor.llm import LLMClient, LLMExplanation, LLMUnavailable
from ubuntu_doctor.llm.client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
)
from ubuntu_doctor.orchestrator import build_snapshot, run_analyzers
from ubuntu_doctor.ranker import rank as rank_by_symptom
from ubuntu_doctor.ui import jsonout, text

COLLECTORS = (DPKG, SYSD, DMSG, JRND)
ANALYZERS = (PUR, SYSH, AAD)

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
    return parser


def _maybe_llm_client(args: argparse.Namespace) -> LLMClient | None:
    if args.no_ai:
        return None
    return LLMClient(
        base_url=args.base_url,
        model=args.model,
        timeout=args.llm_timeout,
    )


async def _explain(
    client: LLMClient | None,
    snapshot,
    hypotheses,
    symptom: str | None,
) -> tuple[LLMExplanation | None, str | None]:
    if client is None:
        return None, None
    try:
        explanation = await client.explain(snapshot, hypotheses, symptom)
        return explanation, None
    except LLMUnavailable as exc:
        return None, str(exc)


async def _run(args: argparse.Namespace) -> int:
    symptom = " ".join(args.symptom) if getattr(args, "symptom", None) else None
    now = datetime.now(timezone.utc)
    window_start = now - args.since

    snapshot = await build_snapshot(COLLECTORS, window_start, now)
    hypotheses = await run_analyzers(ANALYZERS, snapshot)
    hypotheses = rank_by_symptom(hypotheses, symptom)

    client = _maybe_llm_client(args)
    explanation, llm_error = await _explain(client, snapshot, hypotheses, symptom)

    if args.json:
        print(
            jsonout.render(
                snapshot,
                hypotheses,
                explanation=explanation,
                symptom=symptom,
                llm_error=llm_error,
            )
        )
    else:
        print(
            text.render(
                snapshot,
                hypotheses,
                explanation=explanation,
                symptom=symptom,
                llm_error=llm_error,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
