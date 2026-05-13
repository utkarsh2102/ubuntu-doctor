from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.snap_refresh_breakage.plugin import (
    SnapRefreshBreakageAnalyzer,
)
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 13, 14, 30, tzinfo=timezone.utc)


def _refresh(
    snap_name: str,
    status: str = "Done",
    operation: str = "refresh",
    ts: datetime | None = None,
) -> TimelineEvent:
    return TimelineEvent(
        ts=ts or T0,
        kind=EventKind.SNAP_REFRESH,
        source="snap_changes",
        subject=snap_name,
        summary=f"snap {operation}: {snap_name}",
        details={
            "operation": operation,
            "status": status,
            "change_id": "42",
        },
    )


def _denial(
    profile: str, ts: datetime | None = None, operation: str = "open"
) -> TimelineEvent:
    return TimelineEvent(
        ts=ts or T0,
        kind=EventKind.APPARMOR_DENIED,
        source="journald",
        subject=profile,
        summary=f"{profile} denied {operation}",
        details={"profile": profile, "operation": operation, "name": "/x"},
    )


def _snap(events: list[TimelineEvent]) -> Snapshot:
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=14),
        window_end=T0 + timedelta(days=1),
        events=sorted(events, key=lambda e: e.ts),
    )


async def test_clean_refresh_with_no_denial_emits_nothing():
    assert (
        await SnapRefreshBreakageAnalyzer().analyze(
            _snap([_refresh("spotify")])
        )
        == []
    )


async def test_refresh_plus_denial_is_high_confidence():
    snap = _snap(
        [
            _refresh("spotify", ts=T0),
            _denial("snap.spotify.spotify", ts=T0 + timedelta(hours=1)),
            _denial("snap.spotify.spotify", ts=T0 + timedelta(hours=2)),
        ]
    )
    h = (await SnapRefreshBreakageAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.80
    assert any("snap revert spotify" in c for c in h.fix_commands)


async def test_error_status_with_no_denial_is_medium_confidence():
    snap = _snap([_refresh("spotify", status="Error")])
    h = (await SnapRefreshBreakageAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.70


async def test_denial_before_refresh_is_not_correlated():
    snap = _snap(
        [
            _refresh("spotify", ts=T0),
            _denial("snap.spotify.spotify", ts=T0 - timedelta(hours=2)),
        ]
    )
    # Denial precedes refresh — analyzer should not pair them.
    assert await SnapRefreshBreakageAnalyzer().analyze(snap) == []


async def test_denial_outside_window_is_not_correlated():
    snap = _snap(
        [
            _refresh("spotify", ts=T0),
            _denial(
                "snap.spotify.spotify", ts=T0 + timedelta(hours=48)
            ),
        ]
    )
    assert await SnapRefreshBreakageAnalyzer().analyze(snap) == []


async def test_distinct_snaps_get_distinct_hypotheses():
    snap = _snap(
        [
            _refresh("spotify"),
            _refresh("firefox"),
            _denial("snap.spotify.spotify", ts=T0 + timedelta(hours=1)),
            _denial("snap.firefox.firefox", ts=T0 + timedelta(hours=1)),
        ]
    )
    hs = await SnapRefreshBreakageAnalyzer().analyze(snap)
    names = sorted(h.id.split("-")[2] for h in hs)
    assert names == ["firefox", "spotify"]


async def test_risks_steer_user_toward_snap_connect_when_appropriate():
    snap = _snap(
        [
            _refresh("spotify"),
            _denial("snap.spotify.spotify", ts=T0 + timedelta(hours=1)),
        ]
    )
    h = (await SnapRefreshBreakageAnalyzer().analyze(snap))[0]
    risk_text = " ".join(h.risks)
    assert "snap connect" in risk_text
