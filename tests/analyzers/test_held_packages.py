from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.held_packages.plugin import HeldPackagesAnalyzer
from ubuntu_doctor.snapshot import Snapshot

T0 = datetime(2026, 5, 13, tzinfo=timezone.utc)


def _snap(apt_facts: dict | None) -> Snapshot:
    facts = {"apt_log": apt_facts} if apt_facts else {}
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=14),
        window_end=T0,
        facts=facts,
    )


async def test_no_facts_yields_no_hypotheses():
    assert await HeldPackagesAnalyzer().analyze(_snap(None)) == []
    assert await HeldPackagesAnalyzer().analyze(_snap({})) == []


async def test_broken_packages_emit_high_confidence_fix():
    snap = _snap({"broken_packages": ["libfoo-dev", "libbar1"]})
    hs = await HeldPackagesAnalyzer().analyze(snap)
    assert len(hs) == 1
    h = hs[0]
    assert h.confidence == 0.8
    assert any("dpkg --configure -a" in c for c in h.fix_commands)
    assert any("apt install -f" in c for c in h.fix_commands)


async def test_held_packages_have_no_fix_commands():
    snap = _snap({"held_packages": ["nvidia-driver-470"]})
    h = (await HeldPackagesAnalyzer().analyze(snap))[0]
    # Holds are typically intentional — the analyzer must not propose
    # `apt-mark unhold` as a fix.
    assert h.fix_commands == ()
    assert h.confidence == 0.55
    assert "nvidia-driver-470" in h.rationale


async def test_held_plus_broken_is_more_confident():
    snap = _snap(
        {
            "held_packages": ["nvidia-driver-470"],
            "broken_packages": ["libbar1"],
        }
    )
    hs = await HeldPackagesAnalyzer().analyze(snap)
    by_title = {h.title: h for h in hs}
    held = next(h for h in hs if "held" in h.title)
    # Combined case bumps confidence on the held hypothesis.
    assert held.confidence == 0.7
    # Broken hypothesis is still highest.
    assert hs[0].confidence == 0.8


async def test_hypothesis_sorting_descends_by_confidence():
    snap = _snap(
        {
            "held_packages": ["p1"],
            "broken_packages": ["p2"],
        }
    )
    hs = await HeldPackagesAnalyzer().analyze(snap)
    confidences = [h.confidence for h in hs]
    assert confidences == sorted(confidences, reverse=True)
