from __future__ import annotations

from datetime import datetime, timezone

from ubuntu_doctor.collectors.dmesg.plugin import (
    DmesgCollector,
    parse_dmesg_lines,
    parse_iso_timestamp,
)
from ubuntu_doctor.snapshot import EventKind

WINDOW_START = datetime(2026, 5, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 5, 31, tzinfo=timezone.utc)


def test_parse_iso_timestamp():
    ts = parse_iso_timestamp("2026-05-10T14:30:42+0000")
    assert ts == datetime(2026, 5, 10, 14, 30, 42, tzinfo=timezone.utc)
    assert parse_iso_timestamp("garbage") is None
    # Non-UTC offset converted into UTC.
    eastern = parse_iso_timestamp("2026-05-10T10:30:42-0400")
    assert eastern == datetime(2026, 5, 10, 14, 30, 42, tzinfo=timezone.utc)


def test_classifier_picks_up_oom_kill():
    stdout = (
        "2026-05-10T14:30:42+0000 box kernel: Out of memory: Killed process "
        "12345 (greedy) total-vm:1234kB anon-rss:100kB\n"
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    e = events[0]
    assert e.kind == EventKind.OOM_KILL
    assert "greedy" in e.summary
    assert e.details["captures"]["pid"] == "12345"


def test_classifier_picks_up_firmware_failure():
    stdout = (
        "2026-05-10T14:30:42+0000 box kernel: firmware: failed to load "
        "rtl_nic/rtl8125a-3.fw (-2)\n"
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    assert events[0].kind == EventKind.HARDWARE_ERROR
    assert "firmware" in events[0].summary.lower()


def test_classifier_picks_up_regulatory_db():
    stdout = (
        "2026-05-10T14:30:42+0000 box kernel: cfg80211: failed to load "
        "regulatory.db\n"
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    assert events[0].kind == EventKind.HARDWARE_ERROR
    assert "regulatory.db" in events[0].summary


def test_classifier_picks_up_ata_disk_error():
    stdout = (
        "2026-05-10T14:30:42+0000 box kernel: ata1.00: failed command: "
        "READ FPDMA QUEUED\n"
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    assert events[0].kind == EventKind.HARDWARE_ERROR


def test_classifier_picks_up_taint():
    stdout = (
        "2026-05-10T14:30:42+0000 box kernel: nvidia: module verification "
        "failed: signature and/or required key missing - tainting kernel\n"
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    # Either match (signature verification OR taint flag) is acceptable —
    # what matters is we get a single KERNEL_TAINT event.
    assert len(events) == 1
    assert events[0].kind == EventKind.KERNEL_TAINT


def test_apparmor_lines_are_intentionally_skipped():
    # Apparmor denials live in dmesg too, but the journald collector
    # owns them — we drop here to avoid double-counting.
    stdout = (
        "2026-05-10T14:30:42+0000 box kernel: audit: type=1400 "
        'audit(1715347822.122:42): apparmor="DENIED" operation="open" '
        'profile="snap.spotify.spotify" name="/home/u/.config/pulse/cookie"\n'
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    assert events == []


def test_unclassified_lines_are_dropped():
    stdout = (
        "2026-05-10T14:30:42+0000 box kernel: random: crng init done\n"
        "2026-05-10T14:30:43+0000 box kernel: IPv6: ADDRCONF(NETDEV_UP): "
        "wlp0s20f3: link is not ready\n"
    )
    assert parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END) == []


def test_window_filter_drops_outside_events():
    stdout = (
        # Inside window
        "2026-05-10T14:30:42+0000 box kernel: Out of memory: Killed process "
        "1 (a)\n"
        # Before window
        "2026-04-01T14:30:42+0000 box kernel: Out of memory: Killed process "
        "2 (b)\n"
        # After window
        "2026-06-10T14:30:42+0000 box kernel: Out of memory: Killed process "
        "3 (c)\n"
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    assert events[0].details["captures"]["pid"] == "1"


def test_malformed_lines_do_not_crash():
    stdout = (
        "this is not a journalctl line at all\n"
        "2026-05-10T14:30:42+0000 box NOT-kernel: some other process\n"
        "\n"
        "2026-05-10T14:30:43+0000 box kernel: Out of memory: Killed process "
        "9 (real)\n"
    )
    events = parse_dmesg_lines(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1


async def test_collector_emits_events_via_stubbed_runner():
    async def fake_run(args):
        # Sanity: the collector must request the right journalctl invocation.
        assert "--dmesg" in args
        assert "-o" in args and "short-iso" in args
        assert any(a.startswith("--since=@") for a in args)
        return 0, (
            "2026-05-10T14:30:42+0000 box kernel: Out of memory: Killed "
            "process 12345 (greedy)\n"
            "2026-05-10T14:31:00+0000 box kernel: firmware: failed to load "
            "fw.bin\n"
        )

    collector = DmesgCollector(run_command=fake_run)
    result = await collector.collect(WINDOW_START, WINDOW_END)
    assert result.degradation is None
    kinds = sorted(e.kind for e in result.events)
    assert kinds == sorted([EventKind.OOM_KILL, EventKind.HARDWARE_ERROR])


async def test_collector_degrades_on_journalctl_failure():
    async def fake_run(args):
        return 1, ""

    collector = DmesgCollector(run_command=fake_run)
    result = await collector.collect(WINDOW_START, WINDOW_END)
    assert result.events == []
    assert result.degradation is not None
    assert "sudo" in (result.degradation.fix_command or "")
