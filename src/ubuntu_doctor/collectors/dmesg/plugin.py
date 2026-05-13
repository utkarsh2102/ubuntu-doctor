"""Reads the kernel ring buffer via `journalctl --dmesg` and emits
classified events for the things downstream analyzers care about: OOM
kills, kernel taint flags, firmware load failures, hardware errors.

Why `journalctl --dmesg` instead of the `dmesg` binary?
Ubuntu defaults to `kernel.dmesg_restrict=1`, which makes unprivileged
`dmesg` return an empty buffer. `journalctl --dmesg` reads the same data
through journald and works for any user in the `adm` or
`systemd-journal` groups — i.e. most desktop users. If we can't read it
even via journalctl, we degrade with the explicit sudo hint.

This collector intentionally classifies, not firehoses: unmatched
kernel lines are dropped. AppArmor denials specifically are left to the
`journald` collector so they're not double-counted.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ubuntu_doctor.collectors.base import Collector, CollectorResult
from ubuntu_doctor.snapshot import DegradationReport, EventKind, TimelineEvent

CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    kind: EventKind
    summary_template: str


# Order matters: more specific patterns first so a "firmware: failed to
# load" line lands in HARDWARE_ERROR, not the generic taint catch-all.
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        re.compile(
            r"out of memory.*?killed process (?P<pid>\d+)\s*\((?P<comm>[^)]+)\)",
            re.IGNORECASE,
        ),
        EventKind.OOM_KILL,
        "OOM killed {comm} (pid {pid})",
    ),
    _Pattern(
        re.compile(r"\boom-killer\b", re.IGNORECASE),
        EventKind.OOM_KILL,
        "OOM killer invoked",
    ),
    _Pattern(
        re.compile(
            r"firmware:\s+(?:failed|direct-loading\s+failed|bug)",
            re.IGNORECASE,
        ),
        EventKind.HARDWARE_ERROR,
        "firmware load failure",
    ),
    _Pattern(
        re.compile(r"regulatory\.db", re.IGNORECASE),
        EventKind.HARDWARE_ERROR,
        "regulatory.db missing or corrupt",
    ),
    _Pattern(
        re.compile(r"\bmachine check\b|\bmce:\s+hardware", re.IGNORECASE),
        EventKind.HARDWARE_ERROR,
        "machine check exception",
    ),
    _Pattern(
        re.compile(r"aer.*correctable|aer.*uncorrectable", re.IGNORECASE),
        EventKind.HARDWARE_ERROR,
        "PCIe AER error",
    ),
    _Pattern(
        re.compile(
            r"\bata\d+.*?(hard reset|failed command|exception)",
            re.IGNORECASE,
        ),
        EventKind.HARDWARE_ERROR,
        "ATA disk error",
    ),
    _Pattern(
        re.compile(r"\bnvme\b.*?(timeout|i/o error|reset)", re.IGNORECASE),
        EventKind.HARDWARE_ERROR,
        "NVMe error",
    ),
    _Pattern(
        re.compile(
            r"\busb\b.*?(?:cannot enable|device descriptor read.*error|disconnect)",
            re.IGNORECASE,
        ),
        EventKind.HARDWARE_ERROR,
        "USB device error",
    ),
    _Pattern(
        re.compile(r"\bnic link is down\b", re.IGNORECASE),
        EventKind.HARDWARE_ERROR,
        "NIC link down",
    ),
    _Pattern(
        re.compile(
            r"\bdetected (hard|soft) lockup\b",
            re.IGNORECASE,
        ),
        EventKind.HARDWARE_ERROR,
        "CPU lockup detected",
    ),
    _Pattern(
        re.compile(
            r"taint(?:ed)? (?:flag|kernel|module|D)",
            re.IGNORECASE,
        ),
        EventKind.KERNEL_TAINT,
        "kernel taint event",
    ),
    _Pattern(
        re.compile(
            r"module verification failed|module signature verification failed",
            re.IGNORECASE,
        ),
        EventKind.KERNEL_TAINT,
        "module signature verification failed",
    ),
)

# `journalctl -o short-iso` line shape:
#   2026-05-13T09:30:42+0000 hostname kernel: actual message body
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})\s+"
    r"\S+\s+kernel:\s*(?P<body>.*)$"
)


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


def parse_iso_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _classify(body: str) -> tuple[EventKind, str, dict] | None:
    for pat in _PATTERNS:
        match = pat.regex.search(body)
        if not match:
            continue
        groups = match.groupdict()
        try:
            summary = pat.summary_template.format(**groups)
        except KeyError:
            summary = pat.summary_template
        return pat.kind, summary, groups
    return None


def parse_dmesg_lines(
    stdout: str, window_start: datetime, window_end: datetime
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        ts = parse_iso_timestamp(match.group("ts"))
        if ts is None:
            continue
        if ts < window_start or ts > window_end:
            continue
        body = match.group("body")
        # AppArmor denials live in the kernel ring buffer too, but the
        # journald collector is the authority for them (it carries the
        # parsed audit fields). Skip here to avoid double-counting.
        if 'apparmor="DENIED"' in body:
            continue
        classified = _classify(body)
        if classified is None:
            continue
        kind, summary, captures = classified
        subject = captures.get("comm") or kind.value.split(".", 1)[-1]
        events.append(
            TimelineEvent(
                ts=ts,
                kind=kind,
                source="dmesg",
                subject=subject,
                summary=summary,
                details={"raw": body, "captures": captures},
            )
        )
    events.sort(key=lambda e: e.ts)
    return events


class DmesgCollector(Collector):
    id = "dmesg"

    def __init__(self, run_command: CommandRunner | None = None):
        self._run = run_command or _run_subprocess

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        args = [
            "journalctl",
            "--dmesg",
            f"--since=@{int(window_start.timestamp())}",
            f"--until=@{int(window_end.timestamp())}",
            "-o",
            "short-iso",
            "--no-pager",
        ]
        rc, stdout = await self._run(args)
        if rc != 0:
            return CollectorResult(
                events=[],
                degradation=DegradationReport(
                    collector=self.id,
                    reason=f"`journalctl --dmesg` exited {rc}",
                    fix_command="sudo journalctl --dmesg",
                ),
            )
        return CollectorResult(
            events=parse_dmesg_lines(stdout, window_start, window_end)
        )


COLLECTOR = DmesgCollector()
