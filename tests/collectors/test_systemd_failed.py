from __future__ import annotations

from datetime import datetime, timezone

from ubuntu_doctor.collectors.systemd_failed.plugin import (
    SystemdFailedCollector,
    parse_failed_units,
    parse_show_output,
    parse_systemctl_timestamp,
)
from ubuntu_doctor.snapshot import EventKind


def test_parse_failed_units():
    stdout = (
        "pulseaudio.service                       loaded failed failed PulseAudio Sound Server\n"
        "nvidia-persistenced.service              loaded failed failed NVIDIA Persistence Daemon\n"
        "wpa_supplicant.service                   loaded failed failed WPA Supplicant\n"
        "\n"
    )
    assert parse_failed_units(stdout) == [
        "pulseaudio.service",
        "nvidia-persistenced.service",
        "wpa_supplicant.service",
    ]


def test_parse_failed_units_ignores_unknown_unit_types():
    # `systemctl --failed` may include items like `● unit.service` decorated
    # rows when --plain is missing; --plain strips them. Still, be defensive.
    stdout = "garbage row not a unit\n\nfoo.service loaded failed failed Foo\n"
    assert parse_failed_units(stdout) == ["foo.service"]


def test_parse_show_output():
    stdout = (
        "ActiveExitTimestamp=Sat 2026-05-10 14:30:22 UTC\n"
        "Result=exit-code\n"
        "LoadState=loaded\n"
        "Description=PulseAudio Sound Server\n"
    )
    props = parse_show_output(stdout)
    assert props["Result"] == "exit-code"
    assert props["Description"] == "PulseAudio Sound Server"
    assert props["ActiveExitTimestamp"] == "Sat 2026-05-10 14:30:22 UTC"


def test_parse_systemctl_timestamp():
    ts = parse_systemctl_timestamp("Sat 2026-05-10 14:30:22 UTC")
    assert ts == datetime(2026, 5, 10, 14, 30, 22, tzinfo=timezone.utc)
    assert parse_systemctl_timestamp("") is None
    assert parse_systemctl_timestamp("n/a") is None
    assert parse_systemctl_timestamp("garbage") is None


async def test_collector_with_stubbed_runner():
    canned = {
        ("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"): (
            0,
            "pulseaudio.service                       loaded failed failed PulseAudio Sound Server\n"
            "nvidia-persistenced.service              loaded failed failed NVIDIA Persistence Daemon\n",
        ),
        (
            "systemctl",
            "show",
            "pulseaudio.service",
            "--property=ActiveExitTimestamp,Result,LoadState,Description",
            "--no-pager",
        ): (
            0,
            "ActiveExitTimestamp=Sat 2026-05-01 08:14:30 UTC\n"
            "Result=exit-code\n"
            "LoadState=loaded\n"
            "Description=PulseAudio Sound Server\n",
        ),
        (
            "systemctl",
            "show",
            "nvidia-persistenced.service",
            "--property=ActiveExitTimestamp,Result,LoadState,Description",
            "--no-pager",
        ): (
            0,
            "ActiveExitTimestamp=Sat 2026-05-01 08:15:01 UTC\n"
            "Result=exit-code\n"
            "LoadState=loaded\n"
            "Description=NVIDIA Persistence Daemon\n",
        ),
    }

    async def fake_run(args):
        return canned[tuple(args)]

    collector = SystemdFailedCollector(run_command=fake_run)
    result = await collector.collect(
        window_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    assert result.degradation is None
    assert len(result.events) == 2
    subjects = sorted(e.subject for e in result.events)
    assert subjects == ["nvidia-persistenced.service", "pulseaudio.service"]
    for event in result.events:
        assert event.kind == EventKind.SERVICE_FAILED
        assert event.details["timestamp_parsed"] is True
        assert event.details["result"] == "exit-code"


async def test_systemctl_failure_degrades_gracefully():
    async def fake_run(args):
        return 1, ""

    collector = SystemdFailedCollector(run_command=fake_run)
    result = await collector.collect(
        window_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    assert result.events == []
    assert result.degradation is not None
    assert result.degradation.collector == "systemd_failed"


async def test_window_does_not_filter_currently_failed_units():
    # Regression: "currently failed" is a state-of-now fact, not a
    # historical event. A unit that's been failed for months must still
    # be emitted even when the user passes a short --since.
    canned = {
        ("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"): (
            0,
            "ancient.service loaded failed failed Ancient Service\n",
        ),
        (
            "systemctl",
            "show",
            "ancient.service",
            "--property=ActiveExitTimestamp,Result,LoadState,Description",
            "--no-pager",
        ): (
            0,
            "ActiveExitTimestamp=Sat 2024-01-01 00:00:00 UTC\n"
            "Result=exit-code\n"
            "LoadState=loaded\n"
            "Description=Ancient Service\n",
        ),
    }

    async def fake_run(args):
        return canned[tuple(args)]

    collector = SystemdFailedCollector(run_command=fake_run)
    result = await collector.collect(
        window_start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    assert len(result.events) == 1
    # Original ts preserved even though it's well before window_start.
    assert result.events[0].ts == datetime(2024, 1, 1, tzinfo=timezone.utc)


async def test_unparseable_timestamp_falls_back_to_now():
    canned = {
        ("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"): (
            0,
            "weird.service loaded failed failed Weird Service\n",
        ),
        (
            "systemctl",
            "show",
            "weird.service",
            "--property=ActiveExitTimestamp,Result,LoadState,Description",
            "--no-pager",
        ): (
            0,
            "ActiveExitTimestamp=\nResult=signal\nLoadState=loaded\nDescription=Weird Service\n",
        ),
    }

    async def fake_run(args):
        return canned[tuple(args)]

    collector = SystemdFailedCollector(run_command=fake_run)
    result = await collector.collect(
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    assert len(result.events) == 1
    assert result.events[0].details["timestamp_parsed"] is False
