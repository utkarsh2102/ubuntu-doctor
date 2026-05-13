"""System prompt and structured user-prompt builder.

PROMPT_VERSION is part of any future LLM-output cache key. Bump it
whenever the system prompt, output schema, or the shape of the user
prompt changes — old cache entries must miss.
"""

from __future__ import annotations

import json

from ubuntu_doctor.snapshot import Hypothesis, Snapshot

PROMPT_VERSION = "1"

MAX_HYPOTHESES_IN_PROMPT = 8
MAX_RECENT_EVENTS = 25

SYSTEM_PROMPT = """\
You are ubuntu-doctor, a diagnostic assistant for Ubuntu Linux. You receive
a JSON document describing:
- a time window of recent system events from the user's machine;
- hypotheses already produced by deterministic rule-based analyzers, each
  with evidence, confidence, suggested commands, and known risks;
- optionally, a user-reported symptom (e.g. "audio stopped working").

Your job:
1. Re-rank the supplied hypotheses by plausibility given the evidence and,
   if present, the symptom.
2. Explain in plain English WHY each top hypothesis fits (or doesn't).
3. Identify signals you would want to check that aren't in the snapshot.
4. Be honest about uncertainty. Lower confidence rather than guess.

Hard constraints — these are non-negotiable:
- You DO NOT execute commands. You only suggest them as text.
- You DO NOT modify the user's system.
- You MUST NOT invent hypotheses that the input data does not support.
- Every hypothesis_id you emit MUST exactly match an id present in the
  input document under `hypotheses[].id`. Do not invent new ids.
- Output a SINGLE JSON object matching the schema below. No prose, no
  markdown, no commentary outside the JSON object.

Output schema:
{
  "summary": "one paragraph, 2-4 sentences, addressing the symptom if given",
  "ranked_hypotheses": [
    {
      "hypothesis_id": "<must match input hypotheses[].id>",
      "title": "short title for the user",
      "why": "2-4 sentences explaining why this fits the evidence",
      "confidence": 0.0-1.0,
      "commands": ["safe-to-suggest commands the user can copy-paste"],
      "risks": ["caveats the user should know before running the commands"]
    }
  ],
  "what_i_did_not_check": "what additional signals would strengthen or weaken the diagnosis"
}

If no hypothesis is plausible, return an empty ranked_hypotheses array
and explain in `summary` and `what_i_did_not_check`.
"""


def _summarise_for_llm(
    snapshot: Snapshot,
    hypotheses: list[Hypothesis],
    symptom: str | None,
) -> dict:
    return {
        "symptom": symptom,
        "window": {
            "start": snapshot.window_start.isoformat(),
            "end": snapshot.window_end.isoformat(),
        },
        "event_count_total": len(snapshot.events),
        "degradations": [
            {
                "collector": d.collector,
                "reason": d.reason,
                "fix_command": d.fix_command,
            }
            for d in snapshot.degradations
        ],
        "hypotheses": [
            {
                "id": h.id,
                "analyzer": h.analyzer,
                "title": h.title,
                "confidence": h.confidence,
                "rationale": h.rationale,
                "evidence": [
                    {
                        "ts": e.ts.isoformat(),
                        "kind": e.kind.value,
                        "subject": e.subject,
                        "summary": e.summary,
                        "details": e.details,
                    }
                    for e in h.evidence
                ],
                "suggested_commands": list(h.commands),
                "risks": list(h.risks),
            }
            for h in hypotheses[:MAX_HYPOTHESES_IN_PROMPT]
        ],
        "recent_events": [
            {
                "ts": e.ts.isoformat(),
                "kind": e.kind.value,
                "subject": e.subject,
                "summary": e.summary,
            }
            for e in snapshot.events[-MAX_RECENT_EVENTS:]
        ],
    }


def build_user_prompt(
    snapshot: Snapshot,
    hypotheses: list[Hypothesis],
    symptom: str | None = None,
) -> str:
    payload = _summarise_for_llm(snapshot, hypotheses, symptom)
    return json.dumps(payload, indent=2, ensure_ascii=False)
