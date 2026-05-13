"""Reads the systemd journal via `journalctl` and extracts AppArmor
denial events.

Why scope this collector tightly to AppArmor denials for v1?
The journal is huge — pulling everything would blow the LLM token
budget and is mostly noise for diagnosis. AppArmor denials are the
high-signal class that has no other natural collector and that the
`apparmor_denials` analyzer needs. Other journal-derived event types
(service restarts, generic crash patterns) will get their own filtered
queries as we add analyzers that consume them.

**Filter:** `journalctl --grep='apparmor="DENIED"'` (regex). This is
fast even on long journals — journald implements the filter natively
rather than streaming everything to us.

**Permissions:** the user needs read access to the system journal —
membership in `adm` or `systemd-journal`. If not, we get a partial
view; we don't fail loudly because the user may legitimately have only
their own session journal, but the rendered summary still notes when
the journald collector returned zero events.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ubuntu_doctor.collectors.base import Collector, CollectorResult
from ubuntu_doctor.snapshot import DegradationReport, EventKind, TimelineEvent

CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]

_APPARMOR_FIELD_RE = re.compile(r'(\w+)="([^"]*)"')

# Audit-format alternative for boolean apparmor states: `apparmor=DENIED`
# (no quotes) shows up in newer kernels' kaudit output. Match either.
_APPARMOR_HEADER_RE = re.compile(r'apparmor=("DENIED"|DENIED)')


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


def parse_apparmor_message(message: str) -> dict[str, str]:
    """Pull out the `key="value"` pairs from an AppArmor audit message."""
    return {key: value for key, value in _APPARMOR_FIELD_RE.findall(message)}


def parse_journal_jsonl(
    stdout: str, window_start: datetime, window_end: datetime
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _entry_timestamp(entry)
        if ts is None or ts < window_start or ts > window_end:
            continue
        message = entry.get("MESSAGE", "")
        if not isinstance(message, str):
            continue
        if not _APPARMOR_HEADER_RE.search(message):
            continue
        fields = parse_apparmor_message(message)
        profile = fields.get("profile", "unknown")
        operation = fields.get("operation", "?")
        name = fields.get("name") or fields.get("comm") or "?"
        events.append(
            TimelineEvent(
                ts=ts,
                kind=EventKind.APPARMOR_DENIED,
                source="journald",
                subject=profile,
                summary=(
                    f"{profile} denied {operation} on {name}"
                    if name != "?"
                    else f"{profile} denied {operation}"
                ),
                details={
                    "profile": profile,
                    "operation": operation,
                    "name": fields.get("name", ""),
                    "comm": fields.get("comm", ""),
                    "pid": fields.get("pid", ""),
                    "requested_mask": fields.get("requested_mask", ""),
                    "denied_mask": fields.get("denied_mask", ""),
                    "transport": entry.get("_TRANSPORT", ""),
                    "raw": message,
                },
            )
        )
    events.sort(key=lambda e: e.ts)
    return events


def _entry_timestamp(entry: dict) -> datetime | None:
    raw = entry.get("__REALTIME_TIMESTAMP")
    if not raw:
        return None
    try:
        microseconds = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(microseconds / 1_000_000, tz=timezone.utc)


class JournaldCollector(Collector):
    id = "journald"

    def __init__(self, run_command: CommandRunner | None = None):
        self._run = run_command or _run_subprocess

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        args = [
            "journalctl",
            f"--since=@{int(window_start.timestamp())}",
            f"--until=@{int(window_end.timestamp())}",
            "-o",
            "json",
            "--no-pager",
            "--grep",
            'apparmor=("DENIED"|DENIED)',
        ]
        rc, stdout = await self._run(args)
        if rc != 0:
            return CollectorResult(
                events=[],
                degradation=DegradationReport(
                    collector=self.id,
                    reason=f"`journalctl --grep ...` exited {rc}",
                    fix_command=(
                        "sudo usermod -aG systemd-journal $USER  "
                        "# then log out and back in"
                    ),
                ),
            )
        return CollectorResult(
            events=parse_journal_jsonl(stdout, window_start, window_end)
        )


COLLECTOR = JournaldCollector()
