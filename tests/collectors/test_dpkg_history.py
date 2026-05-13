from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ubuntu_doctor.collectors.dpkg_history.plugin import (
    DpkgHistoryCollector,
    parse_line,
)
from ubuntu_doctor.snapshot import EventKind

FIXTURE = Path(__file__).parent.parent / "fixtures" / "dpkg_kernel_nvidia_upgrade.log"


def test_parse_line_upgrade():
    line = "2026-05-01 08:14:10 upgrade pulseaudio:amd64 1:16.1+dfsg1-2ubuntu10 1:16.1+dfsg1-2ubuntu11"
    parsed = parse_line(line)
    assert parsed is not None
    assert parsed.action == "upgrade"
    assert parsed.package == "pulseaudio"
    assert parsed.arch == "amd64"
    assert parsed.old_version == "1:16.1+dfsg1-2ubuntu10"
    assert parsed.new_version == "1:16.1+dfsg1-2ubuntu11"


def test_parse_line_skips_noise():
    assert parse_line("2026-05-01 08:14:00 startup archives unpack") is None
    assert (
        parse_line(
            "2026-05-01 08:14:03 status half-installed linux-image-6.8.0-50-generic:amd64 6.8.0-50.51"
        )
        is None
    )
    assert parse_line("") is None
    assert parse_line("garbage") is None


async def test_collector_emits_events_in_window():
    collector = DpkgHistoryCollector(log_paths=(FIXTURE,))
    result = await collector.collect(
        window_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    assert result.degradation is None

    kinds = [(e.kind, e.subject) for e in result.events]
    assert (EventKind.PACKAGE_INSTALL, "linux-image-6.8.0-50-generic") in kinds
    assert (EventKind.PACKAGE_UPGRADE, "linux-firmware") in kinds
    assert (EventKind.PACKAGE_UPGRADE, "pulseaudio") in kinds
    assert (EventKind.PACKAGE_INSTALL, "htop") in kinds
    assert (EventKind.PACKAGE_REMOVE, "libfoo") in kinds

    # Lines that aren't install/upgrade/remove/purge must be dropped.
    subjects = [e.subject for e in result.events]
    assert "man-db" not in subjects
    assert "archives" not in subjects


async def test_window_filters_events():
    collector = DpkgHistoryCollector(log_paths=(FIXTURE,))
    result = await collector.collect(
        window_start=datetime(2026, 5, 8, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    subjects = {e.subject for e in result.events}
    assert subjects == {"htop", "libfoo"}


async def test_missing_log_files_do_not_crash():
    collector = DpkgHistoryCollector(log_paths=(Path("/nonexistent/dpkg.log"),))
    result = await collector.collect(
        window_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    assert result.events == []
    assert result.degradation is None


async def test_events_sorted_by_timestamp():
    collector = DpkgHistoryCollector(log_paths=(FIXTURE,))
    result = await collector.collect(
        window_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    timestamps = [e.ts for e in result.events]
    assert timestamps == sorted(timestamps)
