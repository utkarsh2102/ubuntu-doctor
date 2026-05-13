"""JSON renderer for `--json` consumers."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from ubuntu_doctor.feedback.store import Incident
from ubuntu_doctor.llm.types import LLMExplanation
from ubuntu_doctor.rag.types import RetrievedSnippet
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent


def _default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, EventKind):
        return value.value
    raise TypeError(f"unserializable: {type(value).__name__}")


def render(
    snapshot: Snapshot,
    hypotheses: list[Hypothesis],
    *,
    explanation: LLMExplanation | None = None,
    symptom: str | None = None,
    llm_error: str | None = None,
    retrieved: list[RetrievedSnippet] | None = None,
    past_incidents: list[Incident] | None = None,
) -> str:
    payload = {
        "schema_version": 2,
        "started_at": snapshot.started_at,
        "symptom": symptom,
        "window": {
            "start": snapshot.window_start,
            "end": snapshot.window_end,
        },
        "events": [_event(e) for e in snapshot.events],
        "degradations": [asdict(d) for d in snapshot.degradations],
        "facts": snapshot.facts,
        "hypotheses": [_hypothesis(h) for h in hypotheses],
        "retrieved_context": [_snippet(s) for s in (retrieved or [])],
        "past_incidents": [_incident(i) for i in (past_incidents or [])],
        "llm": _llm_block(explanation, llm_error),
    }
    return json.dumps(payload, default=_default, indent=2)


def _snippet(s: RetrievedSnippet) -> dict:
    return {
        "source": s.source,
        "kind": s.kind,
        "title": s.title,
        "content": s.content,
        "related_hypothesis_ids": list(s.related_hypothesis_ids),
        "metadata": s.metadata,
    }


def _incident(inc: Incident) -> dict:
    return {
        "id": inc.id,
        "ts": inc.ts,
        "similarity": inc.similarity,
        "fingerprint": inc.fingerprint,
        "chosen_hypothesis_ids": inc.chosen_hypothesis_ids,
        "suggested_fix_commands": inc.suggested_fix_commands,
        "applied_commands": inc.applied_commands,
        "observed_effect": inc.observed_effect,
        "outcome": inc.outcome,
        "notes": inc.notes,
        "revisited_at": inc.revisited_at,
    }


def _llm_block(
    explanation: LLMExplanation | None, llm_error: str | None
) -> dict | None:
    if explanation is None and not llm_error:
        return None
    if explanation is None:
        return {"available": False, "error": llm_error}
    return {
        "available": True,
        "model": explanation.model,
        "prompt_version": explanation.prompt_version,
        "summary": explanation.summary,
        "what_i_did_not_check": explanation.what_i_did_not_check,
        "ranked_hypotheses": [
            {
                "hypothesis_id": rh.hypothesis_id,
                "title": rh.title,
                "why": rh.why,
                "confidence": rh.confidence,
                "fix_commands": list(rh.fix_commands),
                "investigation_steps": list(rh.investigation_steps),
                "risks": list(rh.risks),
            }
            for rh in explanation.ranked_hypotheses
        ],
    }


def _event(e: TimelineEvent) -> dict:
    return {
        "ts": e.ts,
        "kind": e.kind,
        "source": e.source,
        "subject": e.subject,
        "summary": e.summary,
        "details": e.details,
    }


def _hypothesis(h: Hypothesis) -> dict:
    return {
        "id": h.id,
        "analyzer": h.analyzer,
        "title": h.title,
        "confidence": h.confidence,
        "rationale": h.rationale,
        "evidence": [_event(e) for e in h.evidence],
        "fix_commands": list(h.fix_commands),
        "investigation_steps": list(h.investigation_steps),
        "risks": list(h.risks),
    }
