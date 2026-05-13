"""Detects held or broken packages from the apt_log facts and emits
distinct hypotheses for each class.

Two related-but-distinct cases are surfaced separately because their
fixes differ:

  - **Broken packages** (`dpkg --audit` output) are usually a
    consequence of an interrupted install. The fix is concrete:
    `sudo dpkg --configure -a` + `sudo apt install -f`.

  - **Held packages** (`apt-mark showhold`) are usually intentional
    — the user pinned a version to avoid an upgrade. We surface them
    so a downstream analyzer (or the LLM) can correlate with current
    breakage, but we DO NOT propose unholding without context.
"""

from __future__ import annotations

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import Hypothesis, Snapshot


def _broken_hypothesis(broken: list[str]) -> Hypothesis:
    pkgs = ", ".join(broken[:8]) + (f" (+{len(broken) - 8} more)" if len(broken) > 8 else "")
    return Hypothesis(
        id=f"held-packages-broken-{len(broken)}",
        analyzer="held_packages",
        title=f"{len(broken)} package(s) in a broken state",
        confidence=0.8,
        rationale=(
            f"`dpkg --audit` reports {len(broken)} package(s) in a "
            f"broken state ({pkgs}). This usually means a previous "
            "install/upgrade was interrupted (system shutdown, killed "
            "process, dependency conflict). Until these are resolved, "
            "subsequent apt operations will fail or behave unpredictably."
        ),
        evidence=(),
        fix_commands=(
            "sudo dpkg --configure -a",
            "sudo apt install -f",
        ),
        investigation_steps=(
            "sudo dpkg --audit",
            "tail -n 100 /var/log/apt/term.log",
        ),
        risks=(
            "`dpkg --configure -a` resumes any package configuration "
            "that was interrupted. If you stopped the install on "
            "purpose, audit what would resume first.",
        ),
    )


def _held_hypothesis(
    held: list[str], broken_count: int
) -> Hypothesis:
    pkgs = ", ".join(held[:8]) + (f" (+{len(held) - 8} more)" if len(held) > 8 else "")
    # Hold + broken is more interesting than hold alone — usually means
    # the hold is blocking a dependency resolution. Bump confidence in
    # that combined case.
    confidence = 0.55 if broken_count == 0 else 0.7
    rationale_parts = [
        f"{len(held)} package(s) are currently held back from upgrades "
        f"({pkgs}). ",
        "Holds are usually intentional — someone (or unattended-upgrades) "
        "pinned a version to avoid breakage. ",
    ]
    if broken_count > 0:
        rationale_parts.append(
            "Combined with broken packages on this system, the hold may "
            "be blocking dependency resolution. "
        )
    rationale_parts.append(
        "Removing a hold can re-introduce the problem the user pinned "
        "around, so this analyzer never proposes `apt-mark unhold` as a fix."
    )
    return Hypothesis(
        id=f"held-packages-held-{len(held)}",
        analyzer="held_packages",
        title=f"{len(held)} package(s) currently held",
        confidence=confidence,
        rationale="".join(rationale_parts),
        evidence=(),
        fix_commands=(),
        investigation_steps=(
            "apt-mark showhold",
            "apt-cache policy " + " ".join(held[:5]),
            "tail -n 200 /var/log/apt/history.log",
        ),
        risks=(
            "If you decide to lift a hold, do it one package at a time "
            "(`sudo apt-mark unhold <pkg>` then `apt upgrade`) so you "
            "can see which upgrade caused the original issue.",
        ),
    )


class HeldPackagesAnalyzer(Analyzer):
    id = "held_packages"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        facts = snapshot.facts.get("apt_log", {})
        held = list(facts.get("held_packages") or [])
        broken = list(facts.get("broken_packages") or [])

        hypotheses: list[Hypothesis] = []
        if broken:
            hypotheses.append(_broken_hypothesis(broken))
        if held:
            hypotheses.append(_held_hypothesis(held, broken_count=len(broken)))
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses


ANALYZER = HeldPackagesAnalyzer()
