from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.collectors.snap_changes.plugin import (
    SnapChangesCollector,
    parse_snap_changes,
    parse_snap_connections,
    parse_snap_list,
)
from ubuntu_doctor.snapshot import EventKind

T0 = datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)
WINDOW_START = T0 - timedelta(days=1)
WINDOW_END = T0 + timedelta(days=1)


CHANGES_OUT = (
    "ID   Status  Spawn                                 Ready                                 Summary\n"
    "123  Done    2026-05-10T13:30:42+0000              2026-05-10T13:31:00+0000              Refresh of \"spotify\"\n"
    "124  Done    2026-05-10T14:00:00+0000              2026-05-10T14:01:00+0000              Install \"slack\" snap\n"
    "125  Error   2026-05-10T14:15:00+0000              2026-05-10T14:16:00+0000              Refresh of \"firefox\"\n"
    "126  Doing   2026-05-10T14:20:00+0000              -                                     Refresh of \"chromium\"\n"
)


def test_parse_snap_changes_emits_event_per_completed_change():
    events = parse_snap_changes(
        CHANGES_OUT, window_start=WINDOW_START, window_end=WINDOW_END
    )
    subjects = sorted(e.subject for e in events)
    # `chromium` change is still in flight (Doing) — must be skipped.
    assert subjects == ["firefox", "slack", "spotify"]
    spotify = next(e for e in events if e.subject == "spotify")
    assert spotify.kind == EventKind.SNAP_REFRESH
    assert spotify.details["operation"] == "refresh"
    slack = next(e for e in events if e.subject == "slack")
    assert slack.details["operation"] == "install"


def test_parse_snap_changes_captures_error_status():
    events = parse_snap_changes(
        CHANGES_OUT, window_start=WINDOW_START, window_end=WINDOW_END
    )
    firefox = next(e for e in events if e.subject == "firefox")
    assert firefox.details["status"] == "Error"


def test_parse_snap_changes_respects_window():
    events = parse_snap_changes(
        CHANGES_OUT,
        window_start=T0 + timedelta(days=2),
        window_end=T0 + timedelta(days=3),
    )
    assert events == []


def test_parse_snap_list():
    out = parse_snap_list(
        "Name      Version  Rev   Tracking  Publisher  Notes\n"
        "spotify   1.2.3    72    stable    spotify    -\n"
        "core22    20240210 1248  stable    canonical  base\n"
    )
    assert [s["name"] for s in out] == ["spotify", "core22"]
    assert out[0]["publisher"] == "spotify"


def test_parse_snap_connections():
    out = parse_snap_connections(
        "Interface         Plug                          Slot                    Notes\n"
        "audio-playback    spotify:audio-playback        :audio-playback         -\n"
        "home              spotify:home                  :home                   -\n"
    )
    assert len(out) == 2
    assert out[0]["interface"] == "audio-playback"
    assert "spotify" in out[0]["plug"]


async def test_collector_returns_degradation_when_snap_missing():
    async def fake_run(args):
        return 127, ""  # binary not found

    result = await SnapChangesCollector(run_command=fake_run).collect(
        WINDOW_START, WINDOW_END
    )
    assert result.events == []
    assert result.degradation is not None
    assert "snap" in result.degradation.reason.lower()
