"""Captures snap refresh / install / remove operations.

Runs `snap changes --abs-time` for absolute timestamps. Emits
`SNAP_REFRESH` events for each completed change (refresh, install,
remove, connect, disconnect) with `details["operation"]` carrying the
specific verb. Also populates `facts["snap_changes"]` with the current
snap inventory (`snap list`) and connections (`snap connections`)
when those calls succeed — useful context for the
`snap_refresh_breakage` analyzer.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ubuntu_doctor.collectors.base import Collector, CollectorResult
from ubuntu_doctor.snapshot import DegradationReport, EventKind, TimelineEvent

CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]

# `snap changes --abs-time` row example:
#   "123  Done    2024-06-10T10:30:42+0100   2024-06-10T10:31:00+0100   Refresh of "spotify""
_CHANGES_RE = re.compile(
    r"^(?P<id>\d+)\s+(?P<status>\S+)\s+"
    r"(?P<spawn>\S+)\s+(?P<ready>\S+)\s+"
    r"(?P<summary>.+)$"
)

# Summary verbs we recognise. Mapped to the snap name (first quoted token).
_OPERATION_PATTERNS = (
    (re.compile(r'^Refresh of "([^"]+)"'), "refresh"),
    (re.compile(r'^Install "([^"]+)" snap'), "install"),
    (re.compile(r'^Install of "([^"]+)"'), "install"),
    (re.compile(r'^Remove "([^"]+)" snap'), "remove"),
    (re.compile(r'^Remove of "([^"]+)"'), "remove"),
    (re.compile(r'^Connect ([^\s]+) to'), "connect"),
    (re.compile(r'^Disconnect ([^\s]+) from'), "disconnect"),
    (re.compile(r'^Enable "([^"]+)" snap'), "enable"),
    (re.compile(r'^Disable "([^"]+)" snap'), "disable"),
    (re.compile(r'^Revert "([^"]+)" snap'), "revert"),
)


async def _run_subprocess(args: list[str]) -> tuple[int, str]:
    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        return 127, ""
    stdout_bytes, _ = await proc.communicate()
    return proc.returncode or 0, stdout_bytes.decode("utf-8", errors="replace")


def _parse_iso_ts(value: str) -> datetime | None:
    if value in {"", "-", "--"}:
        return None
    # snap emits offsets without a colon: "+0100". datetime.fromisoformat
    # handles that since Python 3.11.
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def _classify_summary(summary: str) -> tuple[str, str] | None:
    for pattern, operation in _OPERATION_PATTERNS:
        match = pattern.search(summary)
        if match:
            return operation, match.group(1)
    return None


def parse_snap_changes(
    stdout: str, *, window_start: datetime, window_end: datetime
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    lines = stdout.splitlines()
    if not lines:
        return events
    for raw in lines:
        line = raw.rstrip()
        if not line or line.startswith("ID ") or line.startswith("ID\t"):
            continue
        match = _CHANGES_RE.match(line)
        if not match:
            continue
        status = match.group("status")
        # Skip ongoing or aborted changes — only completed ones are
        # diagnostic. "Done" is normal completion.
        if status not in {"Done", "Error", "Undone"}:
            continue
        spawn_ts = _parse_iso_ts(match.group("spawn"))
        ready_ts = _parse_iso_ts(match.group("ready"))
        ts = ready_ts or spawn_ts
        if ts is None or ts < window_start or ts > window_end:
            continue
        summary = match.group("summary").strip()
        classified = _classify_summary(summary)
        if classified is None:
            continue
        operation, snap_name = classified
        events.append(
            TimelineEvent(
                ts=ts,
                kind=EventKind.SNAP_REFRESH,
                source="snap_changes",
                subject=snap_name,
                summary=f"snap {operation}: {snap_name}",
                details={
                    "change_id": match.group("id"),
                    "status": status,
                    "operation": operation,
                    "spawn_ts": spawn_ts.isoformat() if spawn_ts else "",
                    "ready_ts": ready_ts.isoformat() if ready_ts else "",
                    "raw_summary": summary,
                },
            )
        )
    return events


def parse_snap_list(stdout: str) -> list[dict]:
    """`snap list` columns: Name  Version  Rev  Tracking  Publisher  Notes."""
    out: list[dict] = []
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        out.append(
            {
                "name": parts[0],
                "version": parts[1],
                "revision": parts[2],
                "tracking": parts[3],
                "publisher": parts[4],
                "notes": " ".join(parts[5:]) if len(parts) > 5 else "",
            }
        )
    return out


def parse_snap_connections(stdout: str) -> list[dict]:
    """`snap connections` columns: Interface  Plug  Slot  Notes."""
    out: list[dict] = []
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        out.append(
            {
                "interface": parts[0],
                "plug": parts[1],
                "slot": parts[2],
                "notes": " ".join(parts[3:]) if len(parts) > 3 else "",
            }
        )
    return out


class SnapChangesCollector(Collector):
    id = "snap_changes"

    def __init__(self, run_command: CommandRunner | None = None):
        self._run = run_command or _run_subprocess

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        changes_rc, changes_out = await self._run(
            ["snap", "changes", "--abs-time"]
        )
        if changes_rc == 127:
            # snap binary not installed at all.
            return CollectorResult(
                events=[],
                degradation=DegradationReport(
                    collector=self.id,
                    reason="`snap` binary not found",
                    fix_command=None,
                ),
            )
        if changes_rc != 0:
            return CollectorResult(
                events=[],
                degradation=DegradationReport(
                    collector=self.id,
                    reason=f"`snap changes` exited {changes_rc}",
                    fix_command="sudo snap changes --abs-time",
                ),
            )

        list_rc, list_out = await self._run(["snap", "list"])
        conn_rc, conn_out = await self._run(["snap", "connections"])

        events = parse_snap_changes(
            changes_out, window_start=window_start, window_end=window_end
        )
        facts: dict = {
            "installed": parse_snap_list(list_out) if list_rc == 0 else [],
            "connections": (
                parse_snap_connections(conn_out) if conn_rc == 0 else []
            ),
        }
        return CollectorResult(events=events, facts=facts)


COLLECTOR = SnapChangesCollector()
