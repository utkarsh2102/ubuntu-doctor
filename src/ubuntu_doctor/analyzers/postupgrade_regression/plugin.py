"""Correlates recent package upgrades with subsequent service failures.

Hypothesis shape: "Package P was upgraded at T, then service S failed
within Δ. The upgrade likely destabilised the service."

Confidence is composed of two signals:

  - Temporal proximity: the closer the failure follows the upgrade,
    the stronger. Falls off linearly over a 24h window.
  - Name-based ownership heuristic: if the failed unit's basename
    matches or is contained in the upgraded package name, that's a
    strong "same package" signal. (A proper analyzer would consult
    `dpkg -S $(systemctl show <unit> -p FragmentPath --value)` to
    confirm ownership; that needs subprocess fan-out per unit and is
    deferred. The temporal+name combination catches the motivating
    real-world cases — pulseaudio after kernel/firmware upgrade,
    NetworkManager after irqbalance, etc. — at acceptable precision.)

Only hypotheses with confidence > 0.2 are emitted; weaker pairs are
correlation noise.
"""

from __future__ import annotations

from datetime import timedelta

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

CORRELATION_WINDOW = timedelta(hours=24)
MIN_CONFIDENCE = 0.2

_NAME_AFFINITY = {
    "linux-firmware": ("bluetooth", "wpa_supplicant", "wifi", "pulseaudio"),
    "linux-image": ("nvidia", "pulseaudio", "wpa_supplicant", "bluetooth"),
    "pulseaudio": ("pulseaudio",),
    "wpa_supplicant": ("wpa_supplicant",),
    "irqbalance": ("network", "irqbalance"),
    "snapd": ("snapd",),
    "nvidia-driver": ("nvidia", "pulseaudio"),
}


def _unit_basename(unit: str) -> str:
    return unit.rsplit(".", 1)[0].lower()


def _name_affinity(package: str, unit: str) -> float:
    """Heuristic score in [0, 1] that `unit` is likely owned by `package`."""
    pkg = package.lower()
    base = _unit_basename(unit)
    if base == pkg:
        return 0.9
    if base in pkg or pkg in base:
        return 0.6
    # Curated affinity for known kernel/firmware/driver fan-out.
    for key, dependents in _NAME_AFFINITY.items():
        if key in pkg and any(d in base for d in dependents):
            return 0.5
    return 0.0


def _temporal_score(delta: timedelta) -> float:
    """Linear falloff from 1.0 at the moment of upgrade to 0.0 at 24h."""
    seconds = delta.total_seconds()
    if seconds < 0:
        return 0.0
    window = CORRELATION_WINDOW.total_seconds()
    if seconds > window:
        return 0.0
    return 1.0 - (seconds / window)


def _combine(temporal: float, affinity: float) -> float:
    # Strong-and weighting: both must contribute. Pure temporal alone caps
    # at 0.4; pure affinity alone caps at 0.4; together they can reach 1.0.
    return 0.4 * temporal + 0.4 * affinity + 0.2 * (temporal * affinity)


def _hypothesis_id(upgrade: TimelineEvent, failure: TimelineEvent) -> str:
    return f"postupgrade-{upgrade.subject}-{failure.subject}-{upgrade.ts.isoformat()}"


def _build_hypothesis(
    upgrade: TimelineEvent, failure: TimelineEvent, confidence: float
) -> Hypothesis:
    delta = failure.ts - upgrade.ts
    old = upgrade.details.get("old_version", "?")
    new = upgrade.details.get("new_version", "?")
    rationale = (
        f"{upgrade.subject} was upgraded {old} → {new} at "
        f"{upgrade.ts.isoformat()}. {failure.subject} failed "
        f"{int(delta.total_seconds() // 60)} min later. The temporal "
        f"correlation and name heuristic suggest the upgrade is the "
        f"likely trigger."
    )
    fix_commands = (
        f"sudo apt install {upgrade.subject}={old}",
        f"sudo apt-mark hold {upgrade.subject}",
        f"sudo systemctl restart {failure.subject}",
    )
    investigation_steps = (
        f"systemctl status {failure.subject}",
        f"journalctl -u {failure.subject} -b --no-pager",
    )
    risks = (
        "Downgrading may pull in older dependencies; review the "
        "transaction before confirming.",
        "If the upgrade brought security fixes, holding the old version "
        "leaves you exposed. Re-evaluate after a real fix lands.",
    )
    return Hypothesis(
        id=_hypothesis_id(upgrade, failure),
        analyzer="postupgrade_regression",
        title=(
            f"{failure.subject} failed shortly after {upgrade.subject} "
            "was upgraded"
        ),
        confidence=round(confidence, 3),
        rationale=rationale,
        evidence=(upgrade, failure),
        fix_commands=fix_commands,
        investigation_steps=investigation_steps,
        risks=risks,
    )


class PostUpgradeRegressionAnalyzer(Analyzer):
    id = "postupgrade_regression"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        upgrades = [
            e
            for e in snapshot.events
            if e.kind in (EventKind.PACKAGE_UPGRADE, EventKind.PACKAGE_INSTALL)
        ]
        failures = [
            e for e in snapshot.events if e.kind == EventKind.SERVICE_FAILED
        ]
        if not upgrades or not failures:
            return []

        hypotheses: list[Hypothesis] = []
        for upgrade in upgrades:
            for failure in failures:
                delta = failure.ts - upgrade.ts
                if delta < timedelta(0) or delta > CORRELATION_WINDOW:
                    continue
                temporal = _temporal_score(delta)
                affinity = _name_affinity(upgrade.subject, failure.subject)
                if affinity == 0.0 and temporal < 0.6:
                    # Without affinity, demand strong temporal proximity.
                    continue
                confidence = _combine(temporal, affinity)
                if confidence < MIN_CONFIDENCE:
                    continue
                hypotheses.append(_build_hypothesis(upgrade, failure, confidence))
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses


ANALYZER = PostUpgradeRegressionAnalyzer()
