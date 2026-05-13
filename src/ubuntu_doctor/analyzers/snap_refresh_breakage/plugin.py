"""Pairs snap refresh / install events with AppArmor denials on the
same snap's profile within a 24h window.

This is the classic Spotify/Slack/Firefox-snap-stopped-working failure:
an auto-refresh from snapd tightens the apparmor profile, a feature
the user relied on (audio access, home-folder writes, etc.) is now
denied, and the user sees nothing — the snap just silently misbehaves.

Hypothesis confidence:
  - Refresh AND ≥1 denial of `snap.<name>.*` within 24h          → 0.80
  - Refresh AND failed change for same snap (status=Error/Undone) → 0.70
  - Snap refresh alone, no denial yet                             → don't fire
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

CORRELATION_WINDOW = timedelta(hours=24)


def _profile_to_snap(profile: str) -> str | None:
    """`snap.spotify.spotify` → `spotify`."""
    parts = profile.split(".")
    if len(parts) >= 3 and parts[0] == "snap":
        return parts[1]
    return None


def _denials_by_snap(
    denials: list[TimelineEvent],
) -> dict[str, list[TimelineEvent]]:
    out: dict[str, list[TimelineEvent]] = defaultdict(list)
    for d in denials:
        snap = _profile_to_snap(d.subject)
        if snap:
            out[snap].append(d)
    return out


def _refreshes_by_snap(
    snap_events: list[TimelineEvent],
) -> dict[str, list[TimelineEvent]]:
    out: dict[str, list[TimelineEvent]] = defaultdict(list)
    for e in snap_events:
        out[e.subject].append(e)
    return out


def _hypothesis(
    snap_name: str,
    refresh: TimelineEvent,
    denials_in_window: list[TimelineEvent],
    refresh_status: str,
) -> Hypothesis:
    operation = refresh.details.get("operation", "refresh")
    rationale_parts = [
        f"snap `{snap_name}` was {operation}ed at "
        f"{refresh.ts.isoformat()} (change "
        f"#{refresh.details.get('change_id', '?')}, status "
        f"`{refresh_status}`). "
    ]
    if denials_in_window:
        operations = sorted(
            {d.details.get("operation", "?") for d in denials_in_window}
        )
        rationale_parts.append(
            f"AppArmor recorded {len(denials_in_window)} denial(s) "
            f"against the snap's profile in the 24h following — "
            f"operations: {', '.join(operations)}. The refresh likely "
            "tightened a rule the snap depends on, or removed/changed "
            "an interface connection."
        )
        confidence = 0.80
    else:
        rationale_parts.append(
            "The change ended in `"
            + refresh_status + "` state without subsequent AppArmor "
            "denials. The snap may have been left in an inconsistent "
            "state by the failed change."
        )
        confidence = 0.70

    fix_commands: tuple[str, ...] = (
        f"sudo snap revert {snap_name}",
    )
    investigation_steps = [
        f"snap connections {snap_name}",
        f"snap info {snap_name}",
        f"journalctl --grep='apparmor=\"DENIED\".*snap.{snap_name}' "
        "--since '1 day ago' --no-pager",
        f"snap changes --abs-time | grep -i '{snap_name}'",
    ]
    risks = [
        f"`snap revert {snap_name}` rolls back to the previously "
        "installed revision. If the refresh contained a security fix, "
        "you re-expose that issue.",
        "Reverting does not pin the snap — the next auto-refresh will "
        "bring back the broken version. Use `snap refresh --hold` if "
        "you need a longer pause while filing a bug.",
    ]
    if denials_in_window:
        # If denials suggest a missing interface, surface a more specific
        # follow-up. The LLM will pick the actual interface from the
        # denial details.
        risks.append(
            "If the cause is a missing snap interface (audio, home, "
            "removable-media, etc.) rather than a regression, the right "
            "fix is `snap connect <name>:<interface>`, NOT revert. The "
            "denial's `name` field (a path) points at which interface "
            "the snap was trying to use."
        )

    return Hypothesis(
        id=f"snap-refresh-{snap_name}-{len(denials_in_window)}",
        analyzer="snap_refresh_breakage",
        title=(
            f"snap `{snap_name}` {operation} broke "
            "(denials following)"
            if denials_in_window
            else f"snap `{snap_name}` {operation} ended in `{refresh_status}`"
        ),
        confidence=confidence,
        rationale="".join(rationale_parts),
        evidence=(refresh,) + tuple(denials_in_window[:5]),
        fix_commands=fix_commands,
        investigation_steps=tuple(investigation_steps),
        risks=tuple(risks),
    )


class SnapRefreshBreakageAnalyzer(Analyzer):
    id = "snap_refresh_breakage"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        snap_events = [
            e for e in snapshot.events if e.kind == EventKind.SNAP_REFRESH
        ]
        if not snap_events:
            return []
        denials_by = _denials_by_snap(
            [e for e in snapshot.events if e.kind == EventKind.APPARMOR_DENIED]
        )
        refreshes_by = _refreshes_by_snap(snap_events)

        hypotheses: list[Hypothesis] = []
        for snap_name, events in refreshes_by.items():
            most_recent = max(events, key=lambda e: e.ts)
            status = most_recent.details.get("status", "Done")
            related_denials = [
                d
                for d in denials_by.get(snap_name, [])
                if timedelta(0) <= (d.ts - most_recent.ts) <= CORRELATION_WINDOW
            ]
            if not related_denials and status == "Done":
                # A clean refresh with nothing following is not
                # interesting; don't emit noise.
                continue
            hypotheses.append(
                _hypothesis(snap_name, most_recent, related_denials, status)
            )
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses


ANALYZER = SnapRefreshBreakageAnalyzer()
