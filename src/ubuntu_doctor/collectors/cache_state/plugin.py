"""Inspects the apt / dpkg cache and lock state.

Looks at:
  - `/var/cache/apt/archives/partial/` — non-empty means an interrupted
    download. Real signal for the cache_health analyzer.
  - `/var/cache/apt/archives/` — total size of downloaded .debs.
  - `/var/lib/dpkg/lock`, `/var/lib/dpkg/lock-frontend` — existence and
    age. Stale locks (>1h old, no holder) often break installs.
  - `/var/lib/apt/lists/` — newest mtime; if very old, `apt update`
    hasn't been run recently.
  - `/var/crash/` — count of reports.

Emits no events; populates `facts["cache_state"]`. The `cache_health`
analyzer reads from it.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from ubuntu_doctor.collectors.base import Collector, CollectorResult

DEFAULT_PATHS = {
    "apt_partial": Path("/var/cache/apt/archives/partial"),
    "apt_archives": Path("/var/cache/apt/archives"),
    "apt_lists": Path("/var/lib/apt/lists"),
    "dpkg_lock": Path("/var/lib/dpkg/lock"),
    "dpkg_lock_frontend": Path("/var/lib/dpkg/lock-frontend"),
    "crash_dir": Path("/var/crash"),
}


def _dir_size_bytes(path: Path) -> int:
    """Sum of regular-file sizes in `path` (one level, not recursive)."""
    try:
        return sum(
            p.stat().st_size
            for p in path.iterdir()
            if p.is_file() and not p.is_symlink()
        )
    except (FileNotFoundError, PermissionError):
        return 0


def _list_partial_packages(path: Path) -> list[str]:
    try:
        return [p.name for p in path.iterdir() if p.is_file()]
    except (FileNotFoundError, PermissionError):
        return []


def _newest_mtime(path: Path) -> float | None:
    """Most recent mtime across the immediate children of `path`."""
    try:
        children = list(path.iterdir())
    except (FileNotFoundError, PermissionError):
        return None
    if not children:
        return None
    return max(c.stat().st_mtime for c in children if not c.is_symlink())


def _lock_state(path: Path, now: float) -> dict | None:
    """Returns `{exists, age_seconds, size}` for a dpkg lock file.

    A zero-byte lock that simply exists is normal (dpkg always creates
    it). What's anomalous is a non-zero-byte lock or one that's been
    held a long time."""
    try:
        st = path.stat()
    except (FileNotFoundError, PermissionError):
        return None
    return {
        "path": str(path),
        "age_seconds": max(0.0, now - st.st_mtime),
        "size_bytes": st.st_size,
    }


def _crash_count(path: Path) -> int:
    try:
        return sum(1 for p in path.iterdir() if p.suffix == ".crash")
    except (FileNotFoundError, PermissionError):
        return 0


class CacheStateCollector(Collector):
    id = "cache_state"

    def __init__(self, paths: dict[str, Path] | None = None, clock=time.time):
        self._paths = paths or DEFAULT_PATHS
        self._clock = clock

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        now = self._clock()
        partial_pkgs = _list_partial_packages(self._paths["apt_partial"])
        archives_size = _dir_size_bytes(self._paths["apt_archives"])
        lists_mtime = _newest_mtime(self._paths["apt_lists"])
        facts: dict = {
            "apt_partial_count": len(partial_pkgs),
            "apt_partial_packages": partial_pkgs[:20],
            "apt_archives_bytes": archives_size,
            "apt_lists_mtime": lists_mtime,
            "apt_lists_age_seconds": (
                now - lists_mtime if lists_mtime is not None else None
            ),
            "dpkg_lock": _lock_state(self._paths["dpkg_lock"], now),
            "dpkg_lock_frontend": _lock_state(
                self._paths["dpkg_lock_frontend"], now
            ),
            "crash_count": _crash_count(self._paths["crash_dir"]),
        }
        return CollectorResult(events=[], facts=facts)


COLLECTOR = CacheStateCollector()
