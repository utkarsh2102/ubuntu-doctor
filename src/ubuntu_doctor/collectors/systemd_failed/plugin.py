"""Lists currently-failed systemd units and stamps each with its last
failure timestamp.

Two `systemctl` calls per run:

  1. `systemctl --failed --no-legend --plain --no-pager` — gives the unit
     names of currently-failed services.
  2. `systemctl show <unit> --property=...` — gives the last
     `ActiveExitTimestamp`, `Result`, `LoadState`, and `Description`.

Both run with `LANG=C` so the timestamp format is stable.

Emits one `SERVICE_FAILED` event per unit, timestamped at its last exit.
If the timestamp can't be parsed, the event is timestamped at the
collector's run time so it still participates in correlation, with the
unparsed value preserved in `details`.

**Window handling:** this collector reports a *current-state fact*, not
historical events. A unit that's failed right now is relevant regardless
of how long ago it last exited, so we ignore the requested
`window_start` / `window_end` and emit every currently-failed unit.
Analyzers can still use the per-event timestamp to filter if they care
(e.g. postupgrade_regression only correlates within 24h of an upgrade).

This collector typically runs unprivileged; user-scoped units may not
appear without `--user`, and that's out of scope for v1.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ubuntu_doctor.collectors.base import Collector, CollectorResult
from ubuntu_doctor.snapshot import DegradationReport, EventKind, TimelineEvent

CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]

_SHOW_PROPERTIES = "ActiveExitTimestamp,Result,LoadState,Description"


async def _run_subprocess(args: list[str]) -> tuple[int, str]:
    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    stdout_bytes, _ = await proc.communicate()
    return proc.returncode or 0, stdout_bytes.decode("utf-8", errors="replace")


def parse_failed_units(stdout: str) -> list[str]:
    units: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        first = line.split()[0]
        if first.endswith((".service", ".socket", ".mount", ".timer", ".path")):
            units.append(first)
    return units


def parse_show_output(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def parse_systemctl_timestamp(value: str) -> datetime | None:
    """Parse a systemctl wall-clock timestamp under LANG=C.

    Format: "Sat 2026-05-10 14:30:22 UTC". Empty / "n/a" → None.
    """
    value = value.strip()
    if not value or value.lower() == "n/a":
        return None
    parts = value.split()
    if len(parts) < 4:
        return None
    date_str, time_str = parts[1], parts[2]
    try:
        dt = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return None
    # Local timezone alignment is not relevant for ordering against dpkg
    # log entries (also stored as naive→UTC). Treat as UTC.
    return dt.replace(tzinfo=timezone.utc)


class SystemdFailedCollector(Collector):
    id = "systemd_failed"

    def __init__(self, run_command: CommandRunner | None = None):
        self._run = run_command or _run_subprocess

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        rc, stdout = await self._run(
            ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"]
        )
        if rc != 0:
            return CollectorResult(
                events=[],
                degradation=DegradationReport(
                    collector=self.id,
                    reason=f"`systemctl --failed` exited {rc}",
                    fix_command=None,
                ),
            )
        units = parse_failed_units(stdout)
        if not units:
            return CollectorResult(events=[])

        shows = await asyncio.gather(
            *(
                self._run(
                    [
                        "systemctl",
                        "show",
                        unit,
                        f"--property={_SHOW_PROPERTIES}",
                        "--no-pager",
                    ]
                )
                for unit in units
            )
        )

        now = datetime.now(timezone.utc)
        events: list[TimelineEvent] = []
        for unit, (show_rc, show_out) in zip(units, shows, strict=True):
            props = parse_show_output(show_out) if show_rc == 0 else {}
            ts = parse_systemctl_timestamp(props.get("ActiveExitTimestamp", ""))
            # Intentionally NOT filtering by window: "currently failed" is a
            # current-state fact, not a historical event. See module docstring.
            event_ts = ts or now
            description = props.get("Description", unit)
            result = props.get("Result", "unknown")
            events.append(
                TimelineEvent(
                    ts=event_ts,
                    kind=EventKind.SERVICE_FAILED,
                    source=self.id,
                    subject=unit,
                    summary=f"{unit} failed ({result}): {description}",
                    details={
                        "result": result,
                        "load_state": props.get("LoadState", ""),
                        "description": description,
                        "timestamp_parsed": ts is not None,
                    },
                )
            )
        events.sort(key=lambda e: e.ts)
        return CollectorResult(events=events)


COLLECTOR = SystemdFailedCollector()
