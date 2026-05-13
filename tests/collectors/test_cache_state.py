from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ubuntu_doctor.collectors.cache_state.plugin import CacheStateCollector

T0 = datetime(2026, 5, 13, tzinfo=timezone.utc)


async def test_collects_partial_packages_and_archive_size(tmp_path: Path):
    paths = _build_layout(tmp_path)
    (paths["apt_partial"] / "linux-firmware_20240318_amd64.deb").write_bytes(b"x" * 10)
    (paths["apt_partial"] / "libssl3_3.0_amd64.deb").write_bytes(b"x" * 20)
    (paths["apt_archives"] / "cached.deb").write_bytes(b"x" * 500)

    result = await CacheStateCollector(paths=paths, clock=lambda: 1000).collect(
        T0, T0
    )
    facts = result.facts or {}
    assert facts["apt_partial_count"] == 2
    assert facts["apt_archives_bytes"] == 500
    assert sorted(facts["apt_partial_packages"]) == [
        "libssl3_3.0_amd64.deb",
        "linux-firmware_20240318_amd64.deb",
    ]


async def test_reports_lock_age_and_size(tmp_path: Path):
    paths = _build_layout(tmp_path)
    lock = paths["dpkg_lock"]
    lock.write_bytes(b"locked")
    # Pretend now=100, the lock was last modified 5s ago.
    import os
    os.utime(lock, (94, 95))

    result = await CacheStateCollector(paths=paths, clock=lambda: 100).collect(
        T0, T0
    )
    facts = result.facts or {}
    lock_info = facts["dpkg_lock"]
    assert lock_info is not None
    assert lock_info["size_bytes"] == 6
    assert lock_info["age_seconds"] == 5.0


async def test_missing_dirs_yield_zero_not_crash(tmp_path: Path):
    # Point at a directory that doesn't exist.
    paths = {
        "apt_partial": tmp_path / "missing_partial",
        "apt_archives": tmp_path / "missing_archives",
        "apt_lists": tmp_path / "missing_lists",
        "dpkg_lock": tmp_path / "missing_lock",
        "dpkg_lock_frontend": tmp_path / "missing_lock_frontend",
        "crash_dir": tmp_path / "missing_crash",
    }
    result = await CacheStateCollector(paths=paths, clock=lambda: 100).collect(
        T0, T0
    )
    facts = result.facts or {}
    assert facts["apt_partial_count"] == 0
    assert facts["apt_archives_bytes"] == 0
    assert facts["dpkg_lock"] is None
    assert facts["dpkg_lock_frontend"] is None
    assert facts["crash_count"] == 0


async def test_apt_lists_age_reported(tmp_path: Path):
    paths = _build_layout(tmp_path)
    (paths["apt_lists"] / "lists_file").write_bytes(b"x")
    import os
    os.utime(paths["apt_lists"] / "lists_file", (50, 50))

    result = await CacheStateCollector(paths=paths, clock=lambda: 100).collect(
        T0, T0
    )
    facts = result.facts or {}
    assert facts["apt_lists_age_seconds"] == 50


def _build_layout(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "apt_partial": tmp_path / "apt_partial",
        "apt_archives": tmp_path / "apt_archives",
        "apt_lists": tmp_path / "apt_lists",
        "dpkg_lock": tmp_path / "dpkg_lock",
        "dpkg_lock_frontend": tmp_path / "dpkg_lock_frontend",
        "crash_dir": tmp_path / "crash",
    }
    for name in ("apt_partial", "apt_archives", "apt_lists", "crash_dir"):
        paths[name].mkdir()
    return paths
