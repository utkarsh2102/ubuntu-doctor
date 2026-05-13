from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.oom_attribution.plugin import OomAttributionAnalyzer
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 13, 14, 30, tzinfo=timezone.utc)


def _kill(comm: str, ts: datetime | None = None) -> TimelineEvent:
    return TimelineEvent(
        ts=ts or T0,
        kind=EventKind.OOM_KILL,
        source="dmesg",
        subject=comm,
        summary=f"OOM killed {comm}",
        details={"captures": {"comm": comm, "pid": "1234"}},
    )


def _failure(unit: str, ts: datetime) -> TimelineEvent:
    return TimelineEvent(
        ts=ts,
        kind=EventKind.SERVICE_FAILED,
        source="systemd_failed",
        subject=unit,
        summary=f"{unit} failed",
        details={"result": "oom-kill"},
    )


def _snap(events: list[TimelineEvent]) -> Snapshot:
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=1),
        window_end=T0 + timedelta(days=1),
        events=sorted(events, key=lambda e: e.ts),
    )


async def test_no_oom_kills_yields_no_hypotheses():
    assert await OomAttributionAnalyzer().analyze(_snap([])) == []


async def test_single_kill_low_confidence():
    h = (await OomAttributionAnalyzer().analyze(_snap([_kill("greedy")])))[0]
    assert h.confidence == 0.40
    # No deterministic fix.
    assert h.fix_commands == ()


async def test_repeated_kills_of_same_process_higher_confidence():
    kills = [_kill("greedy", T0 + timedelta(minutes=i)) for i in range(5)]
    hs = await OomAttributionAnalyzer().analyze(_snap(kills))
    assert len(hs) == 1
    assert hs[0].confidence == 0.80
    assert "5×" in hs[0].title or "5x" in hs[0].title.lower()


async def test_correlation_with_service_failure_boosts_confidence():
    snap = _snap(
        [
            _kill("rsyslogd"),
            _failure("rsyslog.service", T0 + timedelta(minutes=5)),
        ]
    )
    h = (await OomAttributionAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.55


async def test_distinct_killed_processes_get_distinct_hypotheses():
    snap = _snap([_kill("a"), _kill("b"), _kill("a")])
    hs = await OomAttributionAnalyzer().analyze(snap)
    assert len(hs) == 2


async def test_risks_warn_against_naive_workarounds():
    h = (await OomAttributionAnalyzer().analyze(_snap([_kill("greedy")])))[0]
    risk_text = " ".join(h.risks)
    assert "MemoryMax" in risk_text or "OOM killer" in risk_text
