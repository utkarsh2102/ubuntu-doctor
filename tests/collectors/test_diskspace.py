from __future__ import annotations

from datetime import datetime, timezone

from ubuntu_doctor.collectors.diskspace.plugin import (
    DiskspaceCollector,
    parse_df,
    parse_df_inodes,
)

T0 = datetime(2026, 5, 13, tzinfo=timezone.utc)

DF_OUT = (
    "Filesystem     Type     1-blocks       Used  Available Capacity Mounted on\n"
    "/dev/nvme0n1p2 ext4  500000000000 350000000000 130000000000     73% /\n"
    "/dev/nvme0n1p1 vfat    500000000   100000000   400000000     20% /boot/efi\n"
    "tmpfs          tmpfs  8000000000          0  8000000000      0% /run\n"
    "/dev/sda1      ext4   100000000000  98000000000  2000000000     99% /var\n"
)

DF_INODES_OUT = (
    "Filesystem     Type   Inodes  IUsed   IFree IUse% Mounted on\n"
    "/dev/nvme0n1p2 ext4  3000000  500000 2500000  17% /\n"
    "/dev/sda1      ext4  1000000  990000   10000  99% /var\n"
)


def test_parse_df_drops_pseudo_filesystems():
    rows = parse_df(DF_OUT)
    mounts = [r["mount"] for r in rows]
    assert "/" in mounts
    assert "/var" in mounts
    assert "/run" not in mounts  # tmpfs filtered


def test_parse_df_extracts_used_percent():
    rows = parse_df(DF_OUT)
    var = next(r for r in rows if r["mount"] == "/var")
    assert var["used_percent"] == 99
    assert var["total_bytes"] == 100000000000


def test_parse_df_inodes():
    rows = parse_df_inodes(DF_INODES_OUT)
    var = next(r for r in rows if r["mount"] == "/var")
    assert var["inodes_used_percent"] == 99
    assert var["inodes_used"] == 990000


def test_parse_df_handles_malformed_lines():
    out = parse_df("Filesystem Type 1-blocks Used Available Capacity Mount\n"
                   "garbage line\n"
                   "/dev/sda ext4 100 50 50 50% /\n")
    assert len(out) == 1
    assert out[0]["mount"] == "/"


async def test_collector_assembles_facts():
    canned = {
        ("df", "-PT", "--block-size=1"): (0, DF_OUT),
        ("df", "-PTi"): (0, DF_INODES_OUT),
    }

    async def fake_run(args):
        return canned[tuple(args)]

    result = await DiskspaceCollector(run_command=fake_run).collect(T0, T0)
    facts = result.facts or {}
    assert any(fs["mount"] == "/var" for fs in facts["filesystems"])
    assert any(fs["mount"] == "/var" for fs in facts["inodes"])
