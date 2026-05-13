"""Collects per-filesystem usage via `df` and inode usage via `df -i`.

Emits no events. Populates `facts["diskspace"]` with one entry per
real filesystem (skips tmpfs/devtmpfs/squashfs which are uninteresting
for "is /var full?" questions). The `cache_health` analyzer reads
this fact to surface low-space hypotheses.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Awaitable, Callable

from ubuntu_doctor.collectors.base import Collector, CollectorResult

CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]

_SKIP_FS_TYPES = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2"}


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


def parse_df(stdout: str) -> list[dict]:
    """Parse the output of `df -PT --block-size=1` (POSIX format with
    one entry per logical line). Returns list of dicts."""
    rows: list[dict] = []
    lines = stdout.splitlines()
    if not lines:
        return rows
    # Skip header line.
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        source, fstype, total, used, avail, pcent, mount = parts[:7]
        if fstype in _SKIP_FS_TYPES:
            continue
        try:
            total_b = int(total)
            used_b = int(used)
            avail_b = int(avail)
        except ValueError:
            continue
        pcent_int = _parse_percent(pcent)
        rows.append(
            {
                "source": source,
                "fstype": fstype,
                "total_bytes": total_b,
                "used_bytes": used_b,
                "available_bytes": avail_b,
                "used_percent": pcent_int,
                "mount": mount,
            }
        )
    return rows


def parse_df_inodes(stdout: str) -> list[dict]:
    """Parse `df -PTi`. Same shape but reports inode counts."""
    rows: list[dict] = []
    lines = stdout.splitlines()
    if not lines:
        return rows
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        source, fstype, total, used, free, pcent, mount = parts[:7]
        if fstype in _SKIP_FS_TYPES:
            continue
        try:
            total_i = int(total)
            used_i = int(used)
            free_i = int(free)
        except ValueError:
            continue
        rows.append(
            {
                "source": source,
                "fstype": fstype,
                "inodes_total": total_i,
                "inodes_used": used_i,
                "inodes_free": free_i,
                "inodes_used_percent": _parse_percent(pcent),
                "mount": mount,
            }
        )
    return rows


def _parse_percent(value: str) -> int:
    try:
        return int(value.rstrip("%"))
    except ValueError:
        return 0


class DiskspaceCollector(Collector):
    id = "diskspace"

    def __init__(self, run_command: CommandRunner | None = None):
        self._run = run_command or _run_subprocess

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        bytes_rc, bytes_out = await self._run(
            ["df", "-PT", "--block-size=1"]
        )
        inodes_rc, inodes_out = await self._run(["df", "-PTi"])
        facts: dict = {
            "filesystems": parse_df(bytes_out) if bytes_rc == 0 else [],
            "inodes": parse_df_inodes(inodes_out) if inodes_rc == 0 else [],
        }
        return CollectorResult(events=[], facts=facts)


COLLECTOR = DiskspaceCollector()
