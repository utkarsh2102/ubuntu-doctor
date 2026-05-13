"""Groups AppArmor denials by profile and emits one hypothesis per
profile, boosted when an apparmor-related package or snapd was upgraded
in the same window.

**The analyzer deliberately emits no `fix_commands`.** A correct
remediation depends on the specific operation, path, and profile —
context the analyzer has but cannot generalise into a single command
without risking either silencing AppArmor (`aa-complain` / `aa-disable`,
which we refuse to suggest) or making a wrong guess about which snap
interface needs connecting. The LLM gets the full denial details and
is responsible for producing a tailored fix.

What the analyzer does provide: investigation steps that gather the
context the LLM (or the user) needs to commit to a fix — `aa-status`,
`snap connections <name>`, and the on-disk profile path. The
hypothesis's `rationale` explains this delegation explicitly so users
running with `--no-ai` know why no fix command is offered.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

# Package names whose upgrades typically change AppArmor profile
# semantics. A denial seen within this window of such an upgrade gets a
# confidence boost.
_APPARMOR_RELATED_PKGS = (
    "apparmor",
    "apparmor-profiles",
    "snapd",
    "libapparmor1",
)

CORRELATION_WINDOW = timedelta(hours=48)
BASE_CONFIDENCE = 0.5
UPGRADE_BOOST = 0.15
SNAP_UPGRADE_BOOST = 0.20


def _is_snap_profile(profile: str) -> bool:
    return profile.startswith("snap.")


def _snap_name(profile: str) -> str | None:
    # snap.<name>.<command> — return <name>
    parts = profile.split(".")
    if len(parts) < 3 or parts[0] != "snap":
        return None
    return parts[1]


def _related_upgrade(
    profile: str,
    denial_ts,
    upgrades: list[TimelineEvent],
) -> TimelineEvent | None:
    """Find the closest in-window upgrade that plausibly changed the profile."""
    candidates: list[TimelineEvent] = []
    is_snap = _is_snap_profile(profile)
    for u in upgrades:
        pkg = u.subject.lower()
        if is_snap and pkg == "snapd":
            candidates.append(u)
            continue
        if any(rel in pkg for rel in _APPARMOR_RELATED_PKGS):
            candidates.append(u)
            continue
        # Non-snap profile names often share a token with the package
        # that ships them (e.g. profile `usr.bin.firefox` ↔ `firefox`).
        # Be conservative: require a substring match in either direction.
        profile_tokens = {
            tok for tok in profile.replace(".", " ").split() if len(tok) >= 4
        }
        if any(tok in pkg or pkg in tok for tok in profile_tokens):
            candidates.append(u)
    in_window = [
        u for u in candidates if abs(denial_ts - u.ts) <= CORRELATION_WINDOW
    ]
    if not in_window:
        return None
    return min(in_window, key=lambda u: abs(denial_ts - u.ts))


def _hypothesis_id(profile: str, n: int) -> str:
    return f"apparmor-{profile}-{n}"


def _build(
    profile: str,
    denials: list[TimelineEvent],
    correlating_upgrade: TimelineEvent | None,
) -> Hypothesis:
    operations = sorted({d.details.get("operation", "?") for d in denials})
    is_snap = _is_snap_profile(profile)

    if correlating_upgrade is not None:
        pkg_lower = correlating_upgrade.subject.lower()
        if pkg_lower == "snapd" and is_snap:
            confidence = BASE_CONFIDENCE + SNAP_UPGRADE_BOOST
        else:
            confidence = BASE_CONFIDENCE + UPGRADE_BOOST
    else:
        confidence = BASE_CONFIDENCE

    rationale_parts = [
        f"AppArmor blocked {len(denials)} operation(s) for the profile "
        f"`{profile}`. ",
        f"Operations: {', '.join(operations)}. ",
    ]
    if is_snap:
        rationale_parts.append(
            "This is a snap profile — denials most often mean a snap "
            "interface that should be connected isn't. "
        )
    if correlating_upgrade is not None:
        old = correlating_upgrade.details.get("old_version", "?")
        new = correlating_upgrade.details.get("new_version", "?")
        rationale_parts.append(
            f"Package `{correlating_upgrade.subject}` was upgraded "
            f"({old} → {new}) at {correlating_upgrade.ts.isoformat()}, "
            "which can change profile semantics. "
        )
    rationale_parts.append(
        "No deterministic fix is proposed by this analyzer: a safe "
        "remediation depends on the specific operation, path, and "
        "profile. Run with the LLM enabled for a tailored suggestion, "
        "or use the investigation steps below to decide manually."
    )

    investigation_steps: list[str] = [
        (
            "journalctl --grep='apparmor=\"DENIED\"' --since '1 week ago' "
            "--no-pager | head -40"
        ),
        "aa-status",
    ]
    snap_name = _snap_name(profile)
    if snap_name:
        investigation_steps.append(f"snap connections {snap_name}")
        investigation_steps.append(
            f"snap interfaces | grep -E ':\\s+{snap_name}(\\s|$)'"
        )
    else:
        profile_basename = profile.split(":", 1)[-1]
        investigation_steps.append(
            f"ls -l /etc/apparmor.d/ | grep -i '{profile_basename}'"
        )
        investigation_steps.append(
            f"aa-status --profiled | grep -F '{profile}'"
        )

    risks: list[str] = [
        "AppArmor exists to limit what a process can do. Disabling a "
        "profile (`aa-disable`) or putting it in complain mode "
        "(`aa-complain`) removes that protection — fix the missing "
        "connection or file a profile bug instead.",
    ]
    if snap_name:
        risks.append(
            f"`snap connect {snap_name}:<interface>` grants the snap the "
            "capability described by that interface system-wide. Read "
            "`snap interface <name>` before connecting."
        )

    return Hypothesis(
        id=_hypothesis_id(profile, len(denials)),
        analyzer="apparmor_denials",
        title=(
            f"AppArmor denied {len(denials)} operation(s) for {profile}"
        ),
        confidence=round(confidence, 3),
        rationale="".join(rationale_parts).strip(),
        evidence=tuple(denials)
        + ((correlating_upgrade,) if correlating_upgrade is not None else ()),
        fix_commands=(),
        investigation_steps=tuple(investigation_steps),
        risks=tuple(risks),
    )


class ApparmorDenialsAnalyzer(Analyzer):
    id = "apparmor_denials"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        denials = [
            e for e in snapshot.events if e.kind == EventKind.APPARMOR_DENIED
        ]
        if not denials:
            return []

        upgrades = [
            e
            for e in snapshot.events
            if e.kind
            in (EventKind.PACKAGE_UPGRADE, EventKind.PACKAGE_INSTALL)
        ]

        by_profile: dict[str, list[TimelineEvent]] = defaultdict(list)
        for d in denials:
            by_profile[d.subject].append(d)

        hypotheses: list[Hypothesis] = []
        for profile, events in by_profile.items():
            most_recent = max(e.ts for e in events)
            corr = _related_upgrade(profile, most_recent, upgrades)
            hypotheses.append(_build(profile, events, corr))

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses


ANALYZER = ApparmorDenialsAnalyzer()
