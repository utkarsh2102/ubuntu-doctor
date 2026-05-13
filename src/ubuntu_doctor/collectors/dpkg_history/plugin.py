"""Parses /var/log/dpkg.log{,.1{,.gz}} into package install/upgrade/remove events.

Line format (Ubuntu / Debian dpkg, since the 2010s):
    YYYY-MM-DD HH:MM:SS <action> <package>:<arch> <oldver> <newver>

Actions of interest: install, upgrade, remove, purge. Other actions
(status, configure, trigproc, startup) are skipped — they're noise for
correlation.

`/var/log/dpkg.log` is world-readable on standard Ubuntu installs, so
this collector typically does not degrade. If the file is unreadable we
emit a degradation pointing at the right sudo command.
"""

from __future__ import annotations

import asyncio
import gzip
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ubuntu_doctor.collectors.base import Collector, CollectorResult
from ubuntu_doctor.snapshot import DegradationReport, EventKind, TimelineEvent

DEFAULT_LOG_PATHS: tuple[Path, ...] = (
    Path("/var/log/dpkg.log"),
    Path("/var/log/dpkg.log.1"),
)

_ACTION_TO_KIND = {
    "install": EventKind.PACKAGE_INSTALL,
    "upgrade": EventKind.PACKAGE_UPGRADE,
    "remove": EventKind.PACKAGE_REMOVE,
    "purge": EventKind.PACKAGE_PURGE,
}


@dataclass(frozen=True)
class DpkgLogLine:
    ts: datetime
    action: str
    package: str
    arch: str
    old_version: str
    new_version: str


def parse_line(line: str) -> DpkgLogLine | None:
    """Parse one dpkg.log line. Returns None for lines we don't care about."""
    parts = line.strip().split()
    if len(parts) < 4:
        return None
    date_str, time_str, action, *rest = parts
    if action not in _ACTION_TO_KIND:
        return None
    if len(rest) < 3:
        return None
    pkg_arch, old_ver, new_ver = rest[0], rest[1], rest[2]
    package, _, arch = pkg_arch.partition(":")
    try:
        ts = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return DpkgLogLine(
        ts=ts,
        action=action,
        package=package,
        arch=arch,
        old_version=old_ver,
        new_version=new_ver,
    )


def _open(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_log(
    path: Path, window_start: datetime, window_end: datetime
) -> tuple[list[TimelineEvent], DegradationReport | None]:
    try:
        fh = _open(path)
    except FileNotFoundError:
        return [], None
    except PermissionError:
        return [], DegradationReport(
            collector="dpkg_history",
            reason=f"cannot read {path} (permission denied)",
            fix_command=f"sudo cat {path}",
        )
    events: list[TimelineEvent] = []
    with fh:
        for line in fh:
            parsed = parse_line(line)
            if parsed is None:
                continue
            if parsed.ts < window_start or parsed.ts > window_end:
                continue
            kind = _ACTION_TO_KIND[parsed.action]
            if parsed.action == "upgrade":
                summary = (
                    f"{parsed.package} upgraded "
                    f"{parsed.old_version} → {parsed.new_version}"
                )
            elif parsed.action == "install":
                summary = f"{parsed.package} installed at {parsed.new_version}"
            else:
                summary = f"{parsed.package} {parsed.action}d"
            events.append(
                TimelineEvent(
                    ts=parsed.ts,
                    kind=kind,
                    source="dpkg_history",
                    subject=parsed.package,
                    summary=summary,
                    details={
                        "arch": parsed.arch,
                        "old_version": parsed.old_version,
                        "new_version": parsed.new_version,
                    },
                )
            )
    return events, None


class DpkgHistoryCollector(Collector):
    id = "dpkg_history"

    def __init__(self, log_paths: Iterable[Path] | None = None):
        self._log_paths = tuple(log_paths) if log_paths else DEFAULT_LOG_PATHS

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        return await asyncio.to_thread(
            self._collect_sync, window_start, window_end
        )

    def _collect_sync(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        all_events: list[TimelineEvent] = []
        degradation: DegradationReport | None = None
        for path in self._log_paths:
            events, deg = parse_log(path, window_start, window_end)
            all_events.extend(events)
            if deg is not None and degradation is None:
                degradation = deg
        all_events.sort(key=lambda e: e.ts)
        return CollectorResult(events=all_events, degradation=degradation)


COLLECTOR = DpkgHistoryCollector()
