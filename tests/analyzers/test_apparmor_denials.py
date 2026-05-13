from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.apparmor_denials.plugin import (
    ApparmorDenialsAnalyzer,
)
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)


def _denial(
    profile: str = "snap.spotify.spotify",
    operation: str = "open",
    name: str = "/home/u/.config/pulse/cookie",
    comm: str = "spotify",
    ts: datetime | None = None,
) -> TimelineEvent:
    return TimelineEvent(
        ts=ts or T0,
        kind=EventKind.APPARMOR_DENIED,
        source="journald",
        subject=profile,
        summary=f"{profile} denied {operation} on {name}",
        details={
            "profile": profile,
            "operation": operation,
            "name": name,
            "comm": comm,
            "pid": "12345",
            "transport": "audit",
            "raw": "<original>",
        },
    )


def _upgrade(
    pkg: str,
    ts: datetime,
    old: str = "1.0",
    new: str = "1.1",
) -> TimelineEvent:
    return TimelineEvent(
        ts=ts,
        kind=EventKind.PACKAGE_UPGRADE,
        source="dpkg_history",
        subject=pkg,
        summary=f"{pkg} upgraded {old} → {new}",
        details={"old_version": old, "new_version": new},
    )


def _snapshot(events: list[TimelineEvent]) -> Snapshot:
    return Snapshot(
        started_at=T0 + timedelta(hours=2),
        window_start=T0 - timedelta(days=14),
        window_end=T0 + timedelta(days=14),
        events=sorted(events, key=lambda e: e.ts),
    )


async def test_no_denials_yields_no_hypotheses():
    assert await ApparmorDenialsAnalyzer().analyze(_snapshot([])) == []


async def test_baseline_denial_emits_hypothesis_at_base_confidence():
    snap = _snapshot([_denial()])
    hs = await ApparmorDenialsAnalyzer().analyze(snap)
    assert len(hs) == 1
    h = hs[0]
    assert h.confidence == 0.5
    assert h.analyzer == "apparmor_denials"
    assert "snap.spotify.spotify" in h.title
    # The analyzer has no deterministic fix for apparmor denials.
    assert h.fix_commands == ()
    # For snap profiles the investigation must include `snap connections`.
    assert any(
        "snap connections spotify" in c for c in h.investigation_steps
    )
    # Rationale must tell the user a fix isn't proposed and why.
    assert (
        "no deterministic fix" in h.rationale.lower()
        or "tailored" in h.rationale.lower()
        or "llm" in h.rationale.lower()
    )
    # Must warn against silencing AppArmor.
    assert any(
        "aa-disable" in r or "aa-complain" in r or "silencing" in r.lower()
        for r in h.risks
    )


async def test_snap_profile_denial_with_snapd_upgrade_is_boosted_most():
    snap = _snapshot(
        [
            _denial(profile="snap.spotify.spotify"),
            _upgrade("snapd", T0 - timedelta(hours=2)),
        ]
    )
    h = (await ApparmorDenialsAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.7
    # Correlating upgrade must be part of the evidence so the user sees
    # the link in the rendered output.
    subjects = [e.subject for e in h.evidence]
    assert "snapd" in subjects


async def test_apparmor_package_upgrade_gives_smaller_boost():
    snap = _snapshot(
        [
            _denial(profile="snap.firefox.firefox"),
            _upgrade("apparmor", T0 - timedelta(hours=1)),
        ]
    )
    h = (await ApparmorDenialsAnalyzer().analyze(snap))[0]
    # apparmor pkg gives the generic +0.15, not the snap-specific +0.20.
    assert h.confidence == 0.65


async def test_unrelated_package_upgrade_does_not_boost():
    snap = _snapshot(
        [
            _denial(profile="snap.spotify.spotify"),
            _upgrade("htop", T0 - timedelta(hours=1)),
        ]
    )
    h = (await ApparmorDenialsAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.5


async def test_upgrade_outside_correlation_window_does_not_boost():
    snap = _snapshot(
        [
            _denial(profile="snap.spotify.spotify"),
            _upgrade("snapd", T0 - timedelta(days=10)),
        ]
    )
    h = (await ApparmorDenialsAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.5


async def test_multiple_denials_for_same_profile_collapse_into_one():
    snap = _snapshot(
        [
            _denial(profile="snap.spotify.spotify", operation="open",
                    ts=T0),
            _denial(profile="snap.spotify.spotify", operation="exec",
                    ts=T0 + timedelta(seconds=1)),
            _denial(profile="snap.spotify.spotify", operation="mknod",
                    ts=T0 + timedelta(seconds=2)),
        ]
    )
    hs = await ApparmorDenialsAnalyzer().analyze(snap)
    assert len(hs) == 1
    h = hs[0]
    # All three operations should be reflected in the rationale.
    for op in ("open", "exec", "mknod"):
        assert op in h.rationale
    # Evidence should include all three denials.
    assert len(h.evidence) == 3


async def test_distinct_profiles_get_distinct_hypotheses():
    snap = _snapshot(
        [
            _denial(profile="snap.spotify.spotify"),
            _denial(profile="snap.firefox.firefox"),
        ]
    )
    hs = await ApparmorDenialsAnalyzer().analyze(snap)
    assert len(hs) == 2
    profiles = {h.title.split(" for ")[-1] for h in hs}
    assert "snap.spotify.spotify" in profiles
    assert "snap.firefox.firefox" in profiles


async def test_system_profile_suggests_apparmor_d_inspection():
    snap = _snapshot([_denial(profile="usr.bin.firefox")])
    h = (await ApparmorDenialsAnalyzer().analyze(snap))[0]
    # Non-snap profile: must point at /etc/apparmor.d rather than
    # `snap connections`. Investigation only; no deterministic fix.
    assert h.fix_commands == ()
    assert any("/etc/apparmor.d" in c for c in h.investigation_steps)
    assert not any(
        "snap connections" in c for c in h.investigation_steps
    )


async def test_hypotheses_sorted_by_confidence():
    snap = _snapshot(
        [
            _denial(profile="snap.spotify.spotify"),
            _denial(profile="snap.firefox.firefox"),
            _upgrade("snapd", T0 - timedelta(hours=2)),
        ]
    )
    hs = await ApparmorDenialsAnalyzer().analyze(snap)
    # Both profiles get the same +0.20 snapd boost; order is stable but
    # we don't pin it. We only assert the list is in descending order.
    confidences = [h.confidence for h in hs]
    assert confidences == sorted(confidences, reverse=True)
