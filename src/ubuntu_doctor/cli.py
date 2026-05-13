"""Command-line entry point.

v1 vertical slice supports `doctor` (passive diagnosis) with
`--no-ai`, `--json`, and `--since`. The LLM-enabled path is not yet
wired — `--no-ai` is currently implicit.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.postupgrade_regression import ANALYZER as PUR
from ubuntu_doctor.analyzers.systemd_health import ANALYZER as SYSH
from ubuntu_doctor.collectors.dpkg_history import COLLECTOR as DPKG
from ubuntu_doctor.collectors.systemd_failed import COLLECTOR as SYSD
from ubuntu_doctor.orchestrator import build_snapshot, run_analyzers
from ubuntu_doctor.ui import jsonout, text

COLLECTORS = (DPKG, SYSD)
ANALYZERS = (PUR, SYSH)

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doctor",
        description="Diagnose Ubuntu system problems with a local LLM.",
    )
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
        help="Skip the LLM call; deterministic analyzers only. "
        "(Currently implicit; the LLM is not yet wired up.)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    window_start = now - args.since
    snapshot = await build_snapshot(COLLECTORS, window_start, now)
    hypotheses = await run_analyzers(ANALYZERS, snapshot)
    if args.json:
        print(jsonout.render(snapshot, hypotheses))
    else:
        print(text.render(snapshot, hypotheses))
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
