"""System prompt and structured user-prompt builder.

PROMPT_VERSION is part of any future LLM-output cache key. Bump it
whenever the system prompt, output schema, or the shape of the user
prompt changes — old cache entries must miss.
"""

from __future__ import annotations

import json

from ubuntu_doctor.feedback.store import Incident
from ubuntu_doctor.rag.types import RetrievedSnippet
from ubuntu_doctor.snapshot import Hypothesis, Snapshot

# Bumped to 2 because the user prompt now carries retrieved snippets and
# past-incident few-shot examples — different shape than v1.
PROMPT_VERSION = "2"

MAX_HYPOTHESES_IN_PROMPT = 5
MAX_RECENT_EVENTS = 10
MAX_EVIDENCE_PER_HYPOTHESIS = 5
MAX_RETRIEVED_SNIPPETS = 5
MAX_PAST_INCIDENTS = 3

SYSTEM_PROMPT = """\
You are ubuntu-doctor, a diagnostic assistant for Ubuntu Linux. You receive
a JSON document describing:
- a time window of recent system events from the user's machine;
- hypotheses already produced by deterministic rule-based analyzers, each
  with evidence, confidence, suggested actions, and known risks;
- optionally, a user-reported symptom (e.g. "audio stopped working");
- `retrieved_context`: small reference snippets (changelog entries,
  AppArmor profile bodies, apport reports) fetched on-demand from the
  user's machine. Cite these in your `why` text when you use them.
- `past_incidents`: similar incidents this user previously confirmed,
  with the outcome of the fix they tried. Use them as few-shot guidance
  — if a past fix was marked `fixed`, prefer similar fix commands; if
  it was marked `not-fixed` or `made-it-worse`, avoid that fix and say
  why.

Your job:
1. Re-rank the supplied hypotheses by plausibility given the evidence and,
   if present, the symptom.
2. Explain in plain English WHY each top hypothesis fits (or doesn't).
3. Propose concrete, targeted FIX commands that address the root cause.
   The user's analyzers can only produce generic suggestions; you have
   the full denial/failure context (specific path, profile, operation,
   package versions) and you are expected to produce a tailored fix.
4. Identify signals you would want to check that aren't in the snapshot.
5. Be honest about uncertainty. Lower confidence rather than guess.

Distinguish FIX commands from INVESTIGATION steps. They go in different
fields in the output:

- `fix_commands` — specific commands that ATTEMPT TO REPAIR the cause.
  Examples:
    * `sudo apt install pulseaudio=1:16.1+dfsg1-2ubuntu10` — rollback after
      a bad upgrade
    * `snap connect spotify:audio-playback` — restore a missing snap
      interface that the denial pattern indicates is required
    * `sudo dpkg --configure -a` — finish an interrupted package install
    * `sudo systemctl unmask <unit>` — un-mask a unit (only if you're
      confident it was masked accidentally; add a risk if not)
  ONLY include a fix command if you are reasonably confident it
  addresses the cause. If you are NOT confident, leave `fix_commands`
  empty and explain in `why` what you would need to learn.

- `investigation_steps` — read-only commands that GATHER MORE INFO
  before committing to a fix. Examples:
    * `journalctl -u spotify.service -b --no-pager`
    * `aa-status`
    * `dmesg --ctime | grep -i 'out of memory'`
  Use these when the fix is ambiguous, risky, or context-dependent.

- `risks` — caveats the user must know before running anything from
  `fix_commands`. Be specific: which security boundary widens, which
  package versions you'd downgrade, whether a service restart could
  interrupt their session.

Hard constraints — these are non-negotiable:
- You DO NOT execute commands. You only suggest them as text.
- You DO NOT modify the user's system.
- You MUST NOT invent hypotheses that the input data does not support.
- Every hypothesis_id you emit MUST exactly match an id present in the
  input document under `hypotheses[].id`. Do not invent new ids.
- You MUST NOT propose `aa-complain` or `aa-disable` as a fix for an
  AppArmor denial. Silencing AppArmor is not repair; it removes the
  protection. If the right fix is to adjust a profile, suggest reading
  the existing rule and propose the specific addition.
- Every command should be safe to copy-paste verbatim. No placeholders
  like `<your-package>`; use the concrete name from the evidence.
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
      "fix_commands": ["concrete repair commands; empty if you aren't sure"],
      "investigation_steps": ["read-only info-gathering commands"],
      "risks": ["caveats the user should know before running fix_commands"]
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
    retrieved: list[RetrievedSnippet],
    past_incidents: list[Incident],
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
                    }
                    for e in h.evidence[:MAX_EVIDENCE_PER_HYPOTHESIS]
                ],
                "evidence_truncated": (
                    max(0, len(h.evidence) - MAX_EVIDENCE_PER_HYPOTHESIS)
                ),
                "fix_commands_from_analyzer": list(h.fix_commands),
                "investigation_steps_from_analyzer": list(
                    h.investigation_steps
                ),
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
        "retrieved_context": [
            {
                "source": s.source,
                "kind": s.kind,
                "title": s.title,
                "content": s.content,
                "related_hypothesis_ids": list(s.related_hypothesis_ids),
            }
            for s in retrieved[:MAX_RETRIEVED_SNIPPETS]
        ],
        "past_incidents": [
            {
                "id": inc.id,
                "ts": inc.ts.isoformat() if inc.ts else None,
                "similarity": inc.similarity,
                "fingerprint": inc.fingerprint,
                "chosen_hypothesis_ids": inc.chosen_hypothesis_ids,
                "applied_commands": inc.applied_commands,
                "observed_effect": inc.observed_effect,
                "outcome": inc.outcome,
                "notes": inc.notes,
            }
            for inc in past_incidents[:MAX_PAST_INCIDENTS]
        ],
    }


def build_user_prompt(
    snapshot: Snapshot,
    hypotheses: list[Hypothesis],
    symptom: str | None = None,
    *,
    retrieved: list[RetrievedSnippet] | None = None,
    past_incidents: list[Incident] | None = None,
) -> str:
    payload = _summarise_for_llm(
        snapshot,
        hypotheses,
        symptom,
        retrieved or [],
        past_incidents or [],
    )
    return json.dumps(payload, indent=2, ensure_ascii=False)
