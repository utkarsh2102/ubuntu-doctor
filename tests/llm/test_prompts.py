from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ubuntu_doctor.llm.prompts import (
    MAX_HYPOTHESES_IN_PROMPT,
    MAX_RECENT_EVENTS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)


def _event(i: int) -> TimelineEvent:
    return TimelineEvent(
        ts=T0 + timedelta(minutes=i),
        kind=EventKind.PACKAGE_UPGRADE,
        source="dpkg_history",
        subject=f"pkg{i}",
        summary=f"pkg{i} upgraded",
    )


def _snapshot(n_events: int = 3) -> Snapshot:
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=14),
        window_end=T0,
        events=[_event(i) for i in range(n_events)],
    )


def _hyp(id_: str) -> Hypothesis:
    return Hypothesis(
        id=id_,
        analyzer="test",
        title=f"hypothesis {id_}",
        confidence=0.5,
        rationale="because reasons",
        evidence=(_event(0),),
        fix_commands=("apt install foo=1.0",),
        investigation_steps=("journalctl -u foo",),
        risks=("be careful",),
    )


def test_prompt_version_is_set():
    assert PROMPT_VERSION and isinstance(PROMPT_VERSION, str)


def test_system_prompt_includes_hard_constraints():
    assert "DO NOT execute" in SYSTEM_PROMPT
    assert "DO NOT modify" in SYSTEM_PROMPT
    assert "MUST NOT invent" in SYSTEM_PROMPT
    # Must instruct the model to only use existing ids.
    assert "id" in SYSTEM_PROMPT.lower()
    # Must require a single JSON object out.
    assert "JSON" in SYSTEM_PROMPT


def test_user_prompt_includes_symptom_when_provided():
    payload = build_user_prompt(_snapshot(), [_hyp("a")], symptom="audio gone")
    parsed = json.loads(payload)
    assert parsed["symptom"] == "audio gone"


def test_user_prompt_symptom_is_null_when_omitted():
    payload = build_user_prompt(_snapshot(), [_hyp("a")])
    parsed = json.loads(payload)
    assert parsed["symptom"] is None


def test_user_prompt_caps_hypotheses_and_recent_events():
    big_snap = Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=14),
        window_end=T0,
        events=[_event(i) for i in range(MAX_RECENT_EVENTS * 3)],
    )
    many_hypotheses = [_hyp(f"h{i}") for i in range(MAX_HYPOTHESES_IN_PROMPT * 2)]
    payload = build_user_prompt(big_snap, many_hypotheses)
    parsed = json.loads(payload)
    assert len(parsed["hypotheses"]) == MAX_HYPOTHESES_IN_PROMPT
    assert len(parsed["recent_events"]) == MAX_RECENT_EVENTS


def test_user_prompt_preserves_hypothesis_ids():
    payload = build_user_prompt(_snapshot(), [_hyp("hyp-1"), _hyp("hyp-2")])
    parsed = json.loads(payload)
    ids = [h["id"] for h in parsed["hypotheses"]]
    assert ids == ["hyp-1", "hyp-2"]


def test_user_prompt_is_valid_json():
    payload = build_user_prompt(_snapshot(), [_hyp("a")], symptom="x")
    # Just round-trip and make sure it doesn't crash; the other tests
    # cover the shape.
    json.loads(payload)
