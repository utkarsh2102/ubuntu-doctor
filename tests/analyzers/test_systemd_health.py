from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.systemd_health.plugin import SystemdHealthAnalyzer
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)


def _failure(
    unit: str,
    result: str = "exit-code",
    load_state: str = "loaded",
    ts: datetime | None = None,
) -> TimelineEvent:
    return TimelineEvent(
        ts=ts or T0,
        kind=EventKind.SERVICE_FAILED,
        source="systemd_failed",
        subject=unit,
        summary=f"{unit} failed ({result})",
        details={
            "result": result,
            "load_state": load_state,
            "description": unit,
            "timestamp_parsed": ts is not None,
        },
    )


def _snapshot(events: list[TimelineEvent]) -> Snapshot:
    return Snapshot(
        started_at=T0 + timedelta(hours=1),
        window_start=T0 - timedelta(days=14),
        window_end=T0 + timedelta(days=14),
        events=sorted(events, key=lambda e: e.ts),
    )


async def test_no_failures_yields_no_hypotheses():
    assert await SystemdHealthAnalyzer().analyze(_snapshot([])) == []


async def test_oom_kill_is_high_confidence_with_specific_commands():
    snap = _snapshot([_failure("greedy.service", result="oom-kill")])
    hs = await SystemdHealthAnalyzer().analyze(snap)
    assert len(hs) == 1
    h = hs[0]
    assert "OOM killer" in h.title
    assert h.confidence == 0.7
    assert any("dmesg" in c and "out of memory" in c for c in h.commands)
    # Must warn the user against the lazy "just raise the limit" fix.
    assert any("MemoryMax" in r or "leak" in r.lower() for r in h.risks)


async def test_core_dump_suggests_coredumpctl():
    snap = _snapshot([_failure("buggy.service", result="core-dump")])
    h = (await SystemdHealthAnalyzer().analyze(snap))[0]
    assert "core dump" in h.title.lower()
    assert any(c.startswith("coredumpctl info") for c in h.commands)


async def test_timeout_classification():
    snap = _snapshot([_failure("slow.service", result="timeout")])
    h = (await SystemdHealthAnalyzer().analyze(snap))[0]
    assert "timed out" in h.title.lower()
    assert any("TimeoutStart" in c for c in h.commands)
    # Don't suggest bumping the timeout without investigating first.
    assert any("symptom" in r.lower() or "investigat" in r.lower() for r in h.risks)


async def test_signal_classification():
    snap = _snapshot([_failure("ghost.service", result="signal")])
    h = (await SystemdHealthAnalyzer().analyze(snap))[0]
    assert "signal" in h.title.lower()
    assert any("watchdog" in c.lower() or "killed" in c.lower() for c in h.commands)


async def test_loadstate_overrides_result_type():
    # If LoadState is broken, the result type is less informative — the
    # unit-file issue is the actual problem.
    snap = _snapshot(
        [_failure("orphan.service", result="exit-code", load_state="not-found")]
    )
    h = (await SystemdHealthAnalyzer().analyze(snap))[0]
    assert "not-found" in h.title
    assert any("dpkg --audit" in c for c in h.commands)
    assert any("LoadError" in c for c in h.commands)


async def test_masked_loadstate_is_surfaced():
    snap = _snapshot(
        [_failure("disabled.service", result="exit-code", load_state="masked")]
    )
    h = (await SystemdHealthAnalyzer().analyze(snap))[0]
    assert "masked" in h.title
    # Must warn that unmasking may re-enable a deliberately-disabled unit.
    assert any("intentionally" in r.lower() or "deliberately" in r.lower() for r in h.risks)


async def test_generic_exit_code_is_low_confidence_fallback():
    snap = _snapshot([_failure("misc.service", result="exit-code")])
    h = (await SystemdHealthAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.3
    assert "failed state" in h.title


async def test_unknown_result_does_not_crash():
    snap = _snapshot([_failure("weird.service", result="")])
    hs = await SystemdHealthAnalyzer().analyze(snap)
    assert len(hs) == 1
    assert "unknown" in hs[0].title.lower()


async def test_network_cluster_fires_with_two_failures():
    snap = _snapshot(
        [
            _failure("NetworkManager.service", result="exit-code"),
            _failure("systemd-resolved.service", result="exit-code"),
        ]
    )
    hs = await SystemdHealthAnalyzer().analyze(snap)
    titles = [h.title for h in hs]
    # Two individual hypotheses + one cluster.
    assert len(hs) == 3
    cluster = next(h for h in hs if h.analyzer == "systemd_health" and "network units" in h.title)
    assert cluster.confidence > 0.65
    assert any("ip -brief addr" in c for c in cluster.commands)
    # Cluster ranks above the individual generic-exit-code hypotheses.
    assert hs[0].id == cluster.id


async def test_audio_cluster_uses_user_session_commands():
    snap = _snapshot(
        [
            _failure("pipewire.service", result="exit-code"),
            _failure("wireplumber.service", result="signal"),
        ]
    )
    cluster = next(
        h
        for h in await SystemdHealthAnalyzer().analyze(snap)
        if "audio units" in h.title
    )
    assert any("--user" in c for c in cluster.commands)


async def test_single_subsystem_unit_does_not_form_a_cluster():
    snap = _snapshot([_failure("NetworkManager.service", result="exit-code")])
    hs = await SystemdHealthAnalyzer().analyze(snap)
    assert len(hs) == 1
    assert "cluster" not in hs[0].id


async def test_hypotheses_sorted_by_confidence():
    snap = _snapshot(
        [
            _failure("generic.service", result="exit-code"),
            _failure("oom.service", result="oom-kill"),
            _failure("slow.service", result="timeout"),
        ]
    )
    hs = await SystemdHealthAnalyzer().analyze(snap)
    confidences = [h.confidence for h in hs]
    assert confidences == sorted(confidences, reverse=True)
    assert "OOM" in hs[0].title
