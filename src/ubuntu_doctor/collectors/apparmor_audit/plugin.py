"""Reads `/var/log/audit/audit.log` directly and emits APPARMOR_DENIED
events.

This overlaps with the `journald` collector when both auditd and the
systemd journal are running. The `apparmor_denials` analyzer
deduplicates by `(profile, ts, name)` so the overlap doesn't
double-count hypotheses.

Why have both? On auditd-enabled servers, the systemd journal retains
audit messages for only a few hours by default — `/var/log/audit/audit.log`
has weeks. So this collector buys us deeper history on servers
configured that way. On desktop installs without auditd the file
doesn't exist and we degrade silently (no error).

`/var/log/audit/audit.log` requires either the `audit` group or root.
Unprivileged users hit PermissionError; we report it as a degradation
with the sudo command that unlocks it.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

from ubuntu_doctor.collectors.base import Collector, CollectorResult
from ubuntu_doctor.snapshot import DegradationReport, EventKind, TimelineEvent

DEFAULT_AUDIT_PATHS = (
    Path("/var/log/audit/audit.log"),
    Path("/var/log/audit/audit.log.1"),
)

# auditd line example:
#   "type=AVC msg=audit(1717250000.123:42): apparmor=\"DENIED\"
#    operation=\"open\" profile=\"snap.spotify.spotify\" name=\"...\"
#    pid=12345 comm=\"spotify\" requested_mask=\"r\" denied_mask=\"r\""
_AUDIT_HEADER_RE = re.compile(
    r"audit\((?P<epoch>\d+\.\d+):(?P<serial>\d+)\)"
)
_APPARMOR_HEADER_RE = re.compile(r'apparmor=("DENIED"|DENIED)')
_FIELD_RE = re.compile(r'(\w+)="([^"]*)"')


def _read_audit_text(paths: tuple[Path, ...]) -> tuple[str, DegradationReport | None]:
    chunks: list[str] = []
    degradation: DegradationReport | None = None
    seen_permission_error = False
    seen_any_file = False
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                chunks.append(fh.read())
                seen_any_file = True
        except FileNotFoundError:
            continue
        except PermissionError:
            seen_permission_error = True
            continue
        except OSError:
            continue
    if not seen_any_file and seen_permission_error:
        degradation = DegradationReport(
            collector="apparmor_audit",
            reason=(
                "`/var/log/audit/audit.log` exists but isn't readable "
                "by this user"
            ),
            fix_command="sudo cat /var/log/audit/audit.log",
        )
    return "\n".join(chunks), degradation


def parse_audit_log(
    text: str, *, window_start: datetime, window_end: datetime
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for line in text.splitlines():
        if not _APPARMOR_HEADER_RE.search(line):
            continue
        header = _AUDIT_HEADER_RE.search(line)
        if header is None:
            continue
        try:
            ts = datetime.fromtimestamp(
                float(header.group("epoch")), tz=timezone.utc
            )
        except (ValueError, OSError):
            continue
        if ts < window_start or ts > window_end:
            continue
        fields = dict(_FIELD_RE.findall(line))
        profile = fields.get("profile", "unknown")
        operation = fields.get("operation", "?")
        name = fields.get("name") or fields.get("comm") or "?"
        events.append(
            TimelineEvent(
                ts=ts,
                kind=EventKind.APPARMOR_DENIED,
                source="apparmor_audit",
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
                    "audit_serial": header.group("serial"),
                    "raw": line.strip(),
                },
            )
        )
    events.sort(key=lambda e: e.ts)
    return events


class ApparmorAuditCollector(Collector):
    id = "apparmor_audit"

    def __init__(self, audit_paths: tuple[Path, ...] | None = None):
        self._audit_paths = audit_paths or DEFAULT_AUDIT_PATHS

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        text, degradation = await asyncio.to_thread(
            _read_audit_text, self._audit_paths
        )
        if not text:
            # No file or no permission. Degradation may still be set
            # from the PermissionError path.
            return CollectorResult(events=[], degradation=degradation)
        events = parse_audit_log(
            text, window_start=window_start, window_end=window_end
        )
        return CollectorResult(events=events, degradation=degradation)


COLLECTOR = ApparmorAuditCollector()
