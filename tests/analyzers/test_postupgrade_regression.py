from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.postupgrade_regression.plugin import (
    PostUpgradeRegressionAnalyzer,
)
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone.utc)


def _upgrade(pkg: str, ts: datetime, old="1.0", new="1.1") -> TimelineEvent:
    return TimelineEvent(
        ts=ts,
        kind=EventKind.PACKAGE_UPGRADE,
        source="dpkg_history",
        subject=pkg,
        summary=f"{pkg} upgraded {old} → {new}",
        details={"old_version": old, "new_version": new},
    )


def _failure(unit: str, ts: datetime) -> TimelineEvent:
    return TimelineEvent(
        ts=ts,
        kind=EventKind.SERVICE_FAILED,
        source="systemd_failed",
        subject=unit,
        summary=f"{unit} failed",
        details={"result": "exit-code", "timestamp_parsed": True},
    )


def _snapshot(events: list[TimelineEvent]) -> Snapshot:
    events_sorted = sorted(events, key=lambda e: e.ts)
    return Snapshot(
        started_at=T0 + timedelta(hours=2),
        window_start=T0 - timedelta(days=7),
        window_end=T0 + timedelta(days=7),
        events=events_sorted,
    )


async def test_kernel_upgrade_then_pulseaudio_failure_correlates():
    snap = _snapshot(
        [
            _upgrade("linux-firmware", T0, "20240318-0ubuntu3.7", "20240318-0ubuntu3.8"),
            _failure("pulseaudio.service", T0 + timedelta(minutes=30)),
        ]
    )
    hypotheses = await PostUpgradeRegressionAnalyzer().analyze(snap)
    assert len(hypotheses) == 1
    h = hypotheses[0]
    assert "linux-firmware" in h.title
    assert "pulseaudio.service" in h.title
    assert h.confidence > 0.5
    # Suggested rollback should pin the old version. These are real
    # fixes, so they belong in fix_commands, not investigation_steps.
    assert any(
        "apt install linux-firmware=20240318-0ubuntu3.7" in c
        for c in h.fix_commands
    )
    assert any("apt-mark hold linux-firmware" in c for c in h.fix_commands)
    # The analyzer must not promote read-only inspection commands into
    # fix_commands.
    assert not any("journalctl" in c for c in h.fix_commands)


async def test_exact_name_match_is_high_confidence():
    snap = _snapshot(
        [
            _upgrade("pulseaudio", T0),
            _failure("pulseaudio.service", T0 + timedelta(minutes=5)),
        ]
    )
    hypotheses = await PostUpgradeRegressionAnalyzer().analyze(snap)
    assert hypotheses[0].confidence > 0.7


async def test_failure_before_upgrade_is_not_correlated():
    snap = _snapshot(
        [
            _upgrade("pulseaudio", T0),
            _failure("pulseaudio.service", T0 - timedelta(hours=1)),
        ]
    )
    assert await PostUpgradeRegressionAnalyzer().analyze(snap) == []


async def test_failure_outside_window_is_not_correlated():
    snap = _snapshot(
        [
            _upgrade("pulseaudio", T0),
            _failure("pulseaudio.service", T0 + timedelta(hours=48)),
        ]
    )
    assert await PostUpgradeRegressionAnalyzer().analyze(snap) == []


async def test_unrelated_package_and_unit_is_dropped():
    # `htop` has no plausible ownership of network services; far temporal
    # gap means even the temporal-only path doesn't fire.
    snap = _snapshot(
        [
            _upgrade("htop", T0),
            _failure("wpa_supplicant.service", T0 + timedelta(hours=20)),
        ]
    )
    assert await PostUpgradeRegressionAnalyzer().analyze(snap) == []


async def test_strong_temporal_alone_can_fire():
    # No name match, but a failure within minutes of the upgrade is still
    # worth surfacing — the analyzer's temporal-only path must fire.
    snap = _snapshot(
        [
            _upgrade("randompkg", T0),
            _failure("randomunit.service", T0 + timedelta(minutes=1)),
        ]
    )
    hypotheses = await PostUpgradeRegressionAnalyzer().analyze(snap)
    assert len(hypotheses) == 1
    assert 0.2 <= hypotheses[0].confidence < 0.6


async def test_multiple_hypotheses_sorted_by_confidence():
    snap = _snapshot(
        [
            _upgrade("pulseaudio", T0),
            _upgrade("htop", T0),
            _failure("pulseaudio.service", T0 + timedelta(minutes=5)),
            _failure("randomunit.service", T0 + timedelta(minutes=10)),
        ]
    )
    hypotheses = await PostUpgradeRegressionAnalyzer().analyze(snap)
    assert len(hypotheses) >= 1
    confidences = [h.confidence for h in hypotheses]
    assert confidences == sorted(confidences, reverse=True)
    assert "pulseaudio" in hypotheses[0].title


async def test_no_events_yields_no_hypotheses():
    snap = _snapshot([])
    assert await PostUpgradeRegressionAnalyzer().analyze(snap) == []
