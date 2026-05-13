"""Groups OOM-kill events from the dmesg collector by killed process
and surfaces the worst offenders.

OOM-kill is rarely the killed process's fault — it's the symptom of
some *other* process eating memory until the kernel has to free some.
This analyzer therefore emits investigation-only suggestions: there is
no safe deterministic fix without knowing which process was leaking
and whether it was the killed one or the killer.

Confidence scales with repetition:
  - 1 kill, no service restart correlation         → 0.40
  - 1 kill correlating with a recent SERVICE_FAILED → 0.55
  - 2-4 kills of the same process                   → 0.65
  - 5+ kills of the same process                    → 0.80 (strong "leaks"
                                                           signal)
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

CORRELATION_WINDOW = timedelta(hours=1)


def _confidence(count: int, correlated_failure: bool) -> float:
    if count >= 5:
        return 0.80
    if count >= 2:
        return 0.65
    if correlated_failure:
        return 0.55
    return 0.40


def _find_correlated_failure(
    kill: TimelineEvent, failures: list[TimelineEvent]
) -> TimelineEvent | None:
    """A SERVICE_FAILED within ±1h of the OOM-kill whose unit name
    likely matches the killed process. Returns the closest match if
    any."""
    comm = kill.details.get("captures", {}).get("comm") or kill.subject
    comm = comm.lower()
    candidates: list[tuple[timedelta, TimelineEvent]] = []
    for f in failures:
        unit_base = f.subject.rsplit(".", 1)[0].lower()
        if comm and (comm in unit_base or unit_base in comm):
            delta = abs(f.ts - kill.ts)
            if delta <= CORRELATION_WINDOW:
                candidates.append((delta, f))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _hypothesis_for(
    comm: str, kills: list[TimelineEvent], correlated: TimelineEvent | None
) -> Hypothesis:
    confidence = _confidence(len(kills), correlated is not None)
    first_ts = min(k.ts for k in kills)
    last_ts = max(k.ts for k in kills)
    rationale_parts = [
        f"The kernel OOM killer killed `{comm}` {len(kills)} time(s) "
        f"in the analysis window "
    ]
    if len(kills) > 1:
        rationale_parts.append(
            f"(first {first_ts.isoformat()}, last "
            f"{last_ts.isoformat()}). "
        )
    else:
        rationale_parts.append(f"at {first_ts.isoformat()}. ")
    rationale_parts.append(
        "Important: the OOM killer kills the process consuming the most "
        "memory at the moment of pressure — that's often the leaker, "
        "but not always. The right diagnosis depends on whether `"
        + comm + "` was leaking, or whether something else was eating "
        "memory and `"
        + comm + "` was just the largest victim."
    )
    if correlated is not None:
        rationale_parts.append(
            f" The unit `{correlated.subject}` failed within an hour "
            "of an OOM kill — likely the same process restarted by "
            "systemd, then killed again."
        )
    return Hypothesis(
        id=f"oom-{comm}-{len(kills)}",
        analyzer="oom_attribution",
        title=(
            f"{len(kills)}× OOM-kill of `{comm}` in window"
            if len(kills) > 1
            else f"OOM-kill of `{comm}`"
        ),
        confidence=confidence,
        rationale="".join(rationale_parts),
        evidence=tuple(kills)
        + ((correlated,) if correlated is not None else ()),
        fix_commands=(),
        investigation_steps=(
            f"journalctl -k --grep='Killed process.*{comm}' --no-pager",
            "dmesg --ctime | grep -iE 'Killed process|invoked oom-killer'",
            "ps -eo pid,ppid,user,%mem,rss,comm --sort=-rss | head -20",
            f"systemctl status {comm}.service 2>/dev/null",
        ),
        risks=(
            "Do NOT raise the OOM killer's threshold or disable it as a "
            "fix — that just shifts the failure to swap thrashing or "
            "to a different victim. The right action is identifying "
            "the leaking process.",
            "Setting a `MemoryMax=` on a systemd unit only helps if that "
            "unit is the leaker. If something else is eating RAM, the "
            "limit will trigger the same kernel OOM logic.",
        ),
    )


class OomAttributionAnalyzer(Analyzer):
    id = "oom_attribution"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        kills = [
            e for e in snapshot.events if e.kind == EventKind.OOM_KILL
        ]
        if not kills:
            return []
        failures = [
            e for e in snapshot.events if e.kind == EventKind.SERVICE_FAILED
        ]

        by_comm: dict[str, list[TimelineEvent]] = {}
        for k in kills:
            comm = k.details.get("captures", {}).get("comm") or k.subject
            by_comm.setdefault(comm, []).append(k)

        hypotheses: list[Hypothesis] = []
        for comm, group in by_comm.items():
            most_recent = max(group, key=lambda e: e.ts)
            correlated = _find_correlated_failure(most_recent, failures)
            hypotheses.append(_hypothesis_for(comm, group, correlated))
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses


ANALYZER = OomAttributionAnalyzer()
