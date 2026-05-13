"""JSON renderer for `--json` consumers."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent


def _default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, EventKind):
        return value.value
    raise TypeError(f"unserializable: {type(value).__name__}")


def render(snapshot: Snapshot, hypotheses: list[Hypothesis]) -> str:
    payload = {
        "schema_version": 1,
        "started_at": snapshot.started_at,
        "window": {
            "start": snapshot.window_start,
            "end": snapshot.window_end,
        },
        "events": [_event(e) for e in snapshot.events],
        "degradations": [asdict(d) for d in snapshot.degradations],
        "facts": snapshot.facts,
        "hypotheses": [_hypothesis(h) for h in hypotheses],
    }
    return json.dumps(payload, default=_default, indent=2)


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
        "commands": list(h.commands),
        "risks": list(h.risks),
    }
