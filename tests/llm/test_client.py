from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ubuntu_doctor.llm.client import LLMClient, _extract_json_object
from ubuntu_doctor.llm.types import LLMUnavailable
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)


def _snapshot() -> Snapshot:
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=7),
        window_end=T0,
        events=[
            TimelineEvent(
                ts=T0,
                kind=EventKind.SERVICE_FAILED,
                source="systemd_failed",
                subject="pulseaudio.service",
                summary="pulseaudio failed",
            )
        ],
    )


def _hyp(id_: str = "h1") -> Hypothesis:
    return Hypothesis(
        id=id_,
        analyzer="test",
        title="hypothesis title",
        confidence=0.5,
        rationale="rationale text",
    )


def _envelope(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _good_json_content(*ids: str) -> str:
    return json.dumps(
        {
            "summary": "The system looks fine.",
            "ranked_hypotheses": [
                {
                    "hypothesis_id": i,
                    "title": f"title for {i}",
                    "why": f"because of {i}",
                    "confidence": 0.7,
                    "fix_commands": ["apt install foo=1.0"],
                    "investigation_steps": ["journalctl -u foo"],
                    "risks": ["mind the gap"],
                }
                for i in ids
            ],
            "what_i_did_not_check": "the kitchen sink",
        }
    )


def test_extract_json_object_handles_plain_json():
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_strips_code_fences():
    fenced = "```json\n{\"a\": 1}\n```"
    assert _extract_json_object(fenced) == {"a": 1}


def test_extract_json_object_extracts_inner_object_from_prose():
    content = "Sure! Here you go: {\"a\": 1, \"b\": [2, 3]} hope it helps"
    assert _extract_json_object(content) == {"a": 1, "b": [2, 3]}


def test_extract_json_object_raises_on_no_object():
    with pytest.raises(LLMUnavailable):
        _extract_json_object("totally not json")


async def test_explain_happy_path():
    def fake_post(url, body, timeout):
        assert url.endswith("/chat/completions")
        assert body["model"] == "test-model"
        assert "response_format" in body
        return _envelope(_good_json_content("h1"))

    client = LLMClient(
        base_url="http://example/v1",
        model="test-model",
        post_fn=fake_post,
    )
    explanation = await client.explain(_snapshot(), [_hyp("h1")])
    assert explanation.summary == "The system looks fine."
    assert len(explanation.ranked_hypotheses) == 1
    rh = explanation.ranked_hypotheses[0]
    assert rh.hypothesis_id == "h1"
    assert rh.confidence == 0.7
    assert rh.fix_commands == ("apt install foo=1.0",)
    assert rh.investigation_steps == ("journalctl -u foo",)
    assert rh.risks == ("mind the gap",)
    assert explanation.what_i_did_not_check == "the kitchen sink"
    assert explanation.model == "test-model"
    assert explanation.prompt_version
    assert explanation.raw_response


async def test_explain_drops_hallucinated_hypothesis_ids():
    def fake_post(url, body, timeout):
        return _envelope(_good_json_content("h1", "fictional-id"))

    client = LLMClient(post_fn=fake_post)
    explanation = await client.explain(_snapshot(), [_hyp("h1")])
    # The fictional id must be dropped; only h1 survives.
    ids = [rh.hypothesis_id for rh in explanation.ranked_hypotheses]
    assert ids == ["h1"]


async def test_explain_clamps_confidence_to_unit_interval():
    bad_content = json.dumps(
        {
            "summary": "",
            "ranked_hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "title": "x",
                    "why": "y",
                    "confidence": 2.5,
                    "fix_commands": [],
                    "investigation_steps": [],
                    "risks": [],
                },
                {
                    "hypothesis_id": "h1",
                    "title": "x",
                    "why": "y",
                    "confidence": -1,
                    "fix_commands": [],
                    "investigation_steps": [],
                    "risks": [],
                },
            ],
            "what_i_did_not_check": "",
        }
    )

    def fake_post(url, body, timeout):
        return _envelope(bad_content)

    client = LLMClient(post_fn=fake_post)
    explanation = await client.explain(_snapshot(), [_hyp("h1")])
    confidences = [rh.confidence for rh in explanation.ranked_hypotheses]
    assert all(0.0 <= c <= 1.0 for c in confidences)


async def test_explain_legacy_commands_field_maps_to_fix_commands():
    # Older models may emit the legacy `commands` key. The parser must
    # treat it as fix_commands so the user still gets actionable output.
    legacy = json.dumps(
        {
            "summary": "",
            "ranked_hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "title": "x",
                    "why": "y",
                    "confidence": 0.5,
                    "commands": ["apt install foo=1.0"],
                    "risks": [],
                }
            ],
            "what_i_did_not_check": "",
        }
    )

    def fake_post(url, body, timeout):
        return _envelope(legacy)

    client = LLMClient(post_fn=fake_post)
    explanation = await client.explain(_snapshot(), [_hyp("h1")])
    assert len(explanation.ranked_hypotheses) == 1
    rh = explanation.ranked_hypotheses[0]
    assert rh.fix_commands == ("apt install foo=1.0",)
    assert rh.investigation_steps == ()


async def test_explain_handles_unreachable_endpoint():
    def fake_post(url, body, timeout):
        raise LLMUnavailable("connection refused")

    client = LLMClient(post_fn=fake_post)
    with pytest.raises(LLMUnavailable):
        await client.explain(_snapshot(), [_hyp("h1")])


async def test_explain_handles_envelope_without_choices():
    def fake_post(url, body, timeout):
        return {"unexpected": "shape"}

    client = LLMClient(post_fn=fake_post)
    with pytest.raises(LLMUnavailable):
        await client.explain(_snapshot(), [_hyp("h1")])


async def test_explain_handles_non_json_content():
    def fake_post(url, body, timeout):
        return _envelope("Sorry I can't comply.")

    client = LLMClient(post_fn=fake_post)
    with pytest.raises(LLMUnavailable):
        await client.explain(_snapshot(), [_hyp("h1")])


async def test_explain_handles_malformed_inner_json():
    def fake_post(url, body, timeout):
        return _envelope("{this is not valid json at all]")

    client = LLMClient(post_fn=fake_post)
    with pytest.raises(LLMUnavailable):
        await client.explain(_snapshot(), [_hyp("h1")])
