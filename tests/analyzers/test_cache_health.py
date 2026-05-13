from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.cache_health.plugin import CacheHealthAnalyzer
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 13, tzinfo=timezone.utc)


def _snap(*, cache: dict | None = None, disk: dict | None = None,
          events: list[TimelineEvent] | None = None) -> Snapshot:
    facts = {}
    if cache is not None:
        facts["cache_state"] = cache
    if disk is not None:
        facts["diskspace"] = disk
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=14),
        window_end=T0,
        events=events or [],
        facts=facts,
    )


async def test_no_facts_yields_no_hypotheses():
    assert await CacheHealthAnalyzer().analyze(_snap()) == []


async def test_partial_downloads_emit_fix_command():
    snap = _snap(
        cache={
            "apt_partial_count": 2,
            "apt_partial_packages": ["foo.deb", "bar.deb"],
        }
    )
    hs = await CacheHealthAnalyzer().analyze(snap)
    h = next(h for h in hs if "partial" in h.title.lower() or "interrupted" in h.title.lower())
    assert h.confidence == 0.7
    assert "sudo apt clean" in h.fix_commands


async def test_critical_disk_pressure_is_high_confidence():
    snap = _snap(
        disk={
            "filesystems": [
                {
                    "source": "/dev/sda1",
                    "fstype": "ext4",
                    "total_bytes": 100_000_000_000,
                    "used_bytes": 99_000_000_000,
                    "available_bytes": 1_000_000_000,
                    "used_percent": 99,
                    "mount": "/var",
                }
            ],
            "inodes": [],
        }
    )
    h = (await CacheHealthAnalyzer().analyze(snap))[0]
    assert "/var" in h.title
    assert h.confidence == 0.85  # ≥98%
    # /var fix path includes apt clean + journal vacuum.
    assert any("apt clean" in c for c in h.fix_commands)


async def test_non_critical_mount_below_threshold_is_silent():
    snap = _snap(
        disk={
            "filesystems": [
                {
                    "source": "/dev/sdb1",
                    "fstype": "ext4",
                    "total_bytes": 100,
                    "used_bytes": 91,
                    "available_bytes": 9,
                    "used_percent": 91,
                    "mount": "/data",
                }
            ],
            "inodes": [],
        }
    )
    # /data isn't critical, and 91% < 95% non-critical threshold.
    assert await CacheHealthAnalyzer().analyze(snap) == []


async def test_inode_pressure_fires():
    snap = _snap(
        disk={
            "filesystems": [],
            "inodes": [
                {
                    "source": "/dev/sda",
                    "fstype": "ext4",
                    "inodes_total": 1_000_000,
                    "inodes_used": 950_000,
                    "inodes_free": 50_000,
                    "inodes_used_percent": 95,
                    "mount": "/",
                }
            ],
        }
    )
    h = (await CacheHealthAnalyzer().analyze(snap))[0]
    assert "inode" in h.title.lower()


async def test_stale_apt_lists_emits_low_confidence_advisory():
    snap = _snap(
        cache={
            "apt_partial_count": 0,
            "apt_lists_age_seconds": 60 * 24 * 3600,  # 60 days
        }
    )
    hs = await CacheHealthAnalyzer().analyze(snap)
    h = next(h for h in hs if "apt package lists" in h.title.lower())
    assert h.confidence == 0.45
    assert "sudo apt update" in h.fix_commands


async def test_boot_kernel_pressure_fires_on_install_correlation():
    install = TimelineEvent(
        ts=T0,
        kind=EventKind.PACKAGE_INSTALL,
        source="dpkg_history",
        subject="linux-image-6.8.0-50-generic",
        summary="kernel installed",
        details={"new_version": "6.8.0-50.51"},
    )
    snap = _snap(
        disk={
            "filesystems": [
                {
                    "source": "/dev/sda1",
                    "fstype": "ext4",
                    "total_bytes": 500_000_000,
                    "used_bytes": 425_000_000,
                    "available_bytes": 75_000_000,
                    "used_percent": 85,
                    "mount": "/boot",
                }
            ],
            "inodes": [],
        },
        events=[install],
    )
    hs = await CacheHealthAnalyzer().analyze(snap)
    h = next(h for h in hs if "kernel" in h.title.lower())
    assert h.confidence == 0.8
    assert any("autoremove" in c for c in h.fix_commands)


async def test_stale_zero_byte_lock_is_silent():
    # A zero-byte lock is normal; only complain about non-zero ones.
    snap = _snap(
        cache={
            "apt_partial_count": 0,
            "dpkg_lock": {"path": "/var/lib/dpkg/lock", "age_seconds": 90000, "size_bytes": 0},
        }
    )
    assert await CacheHealthAnalyzer().analyze(snap) == []
