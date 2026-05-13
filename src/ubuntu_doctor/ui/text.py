"""Plain-text renderer for the human at the terminal."""

from __future__ import annotations

import textwrap

from ubuntu_doctor.feedback.store import Incident
from ubuntu_doctor.llm.types import LLMExplanation
from ubuntu_doctor.rag.types import RetrievedSnippet
from ubuntu_doctor.snapshot import Hypothesis, Snapshot
from ubuntu_doctor.ui.colors import Palette, supports_color

MAX_HYPOTHESES_DISPLAYED = 10
MAX_EVIDENCE_DISPLAYED = 5
MAX_SNIPPET_PREVIEW_CHARS = 400


def render(
    snapshot: Snapshot,
    hypotheses: list[Hypothesis],
    *,
    explanation: LLMExplanation | None = None,
    symptom: str | None = None,
    llm_error: str | None = None,
    retrieved: list[RetrievedSnippet] | None = None,
    past_incidents: list[Incident] | None = None,
    color: bool | None = None,
) -> str:
    retrieved = retrieved or []
    past_incidents = past_incidents or []
    p = Palette(enabled=supports_color() if color is None else color)

    lines: list[str] = []
    window = (
        f"{snapshot.window_start.isoformat()} → "
        f"{snapshot.window_end.isoformat()}"
    )
    sources = {e.source for e in snapshot.events} | set(snapshot.facts.keys())
    lines.append(p.bold(f"ubuntu-doctor — window: {window}"))
    lines.append(
        p.dim(f"  Collected {len(snapshot.events)} events from {len(sources)} sources")
    )
    if retrieved:
        lines.append(
            p.dim(f"  Retrieved {len(retrieved)} reference snippet(s) on-demand")
        )
    if past_incidents:
        lines.append(
            p.dim(
                f"  Matched {len(past_incidents)} similar past incident(s) from "
                "local feedback store"
            )
        )
    if symptom:
        lines.append(f"  Symptom: {p.bold(repr(symptom))}")
    lines.append("")

    if explanation is not None:
        _append_llm_explanation(lines, explanation, hypotheses, p)
        lines.append("")
        lines.append(p.bold("Underlying deterministic findings:"))
    elif llm_error:
        lines.append(p.yellow(f"LLM unavailable — {llm_error}"))
        lines.append(p.dim("Falling back to deterministic findings only."))
        lines.append("")

    if not hypotheses:
        lines.append(p.green("No correlations found."))
        lines.append(
            p.dim(
                "  (this means: no rule-based analyzer matched. Run with "
                "LLM enabled, or `doctor why <symptom>`, to dig deeper.)"
            )
        )
    else:
        shown = hypotheses[:MAX_HYPOTHESES_DISPLAYED]
        suffix = (
            f" (showing top {MAX_HYPOTHESES_DISPLAYED})"
            if len(hypotheses) > MAX_HYPOTHESES_DISPLAYED
            else ""
        )
        lines.append(p.bold(f"Likely causes ({len(hypotheses)}){suffix}:"))
        for i, h in enumerate(shown, 1):
            _append_hypothesis(lines, i, h, p)

    if past_incidents:
        lines.append("")
        lines.append(p.bold("Similar past incidents in your feedback store:"))
        for inc in past_incidents:
            sim = (
                f"{inc.similarity:.0%}"
                if inc.similarity is not None
                else "?"
            )
            ts = inc.ts.isoformat() if inc.ts else "?"
            lines.append(
                f"  - #{inc.id} "
                f"{p.dim(f'({ts}, similarity {sim})')} → "
                f"outcome: {p.bold(inc.outcome)}"
            )
            if inc.observed_effect:
                lines.append(
                    textwrap.fill(
                        f"observed: {inc.observed_effect}",
                        width=72,
                        initial_indent="      ",
                        subsequent_indent="      ",
                    )
                )
            if inc.applied_commands:
                lines.append(p.dim("      ran:"))
                for cmd in inc.applied_commands[:5]:
                    lines.append(f"        $ {p.green(cmd)}")
                if len(inc.applied_commands) > 5:
                    lines.append(
                        p.dim(f"        … and {len(inc.applied_commands) - 5} more")
                    )

    if retrieved:
        lines.append("")
        lines.append(p.bold("Reference material consulted:"))
        for snippet in retrieved:
            lines.append(f"  - {p.dim(f'[{snippet.kind}]')} {snippet.title}")
            preview = snippet.content[:MAX_SNIPPET_PREVIEW_CHARS].strip()
            if len(snippet.content) > MAX_SNIPPET_PREVIEW_CHARS:
                preview += " …"
            for line in preview.splitlines():
                lines.append(p.dim(f"      {line}"))

    if snapshot.degradations:
        lines.append("")
        lines.append(p.yellow("What I couldn't see:"))
        for deg in snapshot.degradations:
            lines.append(p.yellow(f"  - {deg.collector}: {deg.reason}"))
            if deg.fix_command:
                lines.append(f"      to unlock: {p.green(deg.fix_command)}")

    return "\n".join(lines)


def _append_llm_explanation(
    lines: list[str],
    explanation: LLMExplanation,
    hypotheses: list[Hypothesis],
    p: Palette,
) -> None:
    lines.append(
        p.bold(f"ubuntu-doctor — diagnosis ")
        + p.dim(f"(model: {explanation.model})")
    )
    if explanation.summary:
        lines.append(
            textwrap.fill(
                explanation.summary,
                width=78,
                initial_indent="  ",
                subsequent_indent="  ",
            )
        )
    by_id = {h.id: h for h in hypotheses}
    if explanation.ranked_hypotheses:
        lines.append("")
        lines.append(p.bold("Top hypotheses:"))
        for i, rh in enumerate(explanation.ranked_hypotheses, 1):
            lines.append("")
            lines.append(
                f"  [{i}] {p.red(rh.title)}  "
                + p.dim(f"(LLM confidence {rh.confidence:.2f})")
            )
            origin = by_id.get(rh.hypothesis_id)
            if origin is not None:
                lines.append(
                    p.dim(
                        f"      origin: {origin.analyzer} "
                        f"(rule confidence {origin.confidence:.2f})"
                    )
                )
            if rh.why:
                lines.append(
                    textwrap.fill(
                        rh.why,
                        width=72,
                        initial_indent="      ",
                        subsequent_indent="      ",
                    )
                )
            if rh.fix_commands:
                lines.append(p.dim("      suggested fix commands (NOT executed):"))
                for cmd in rh.fix_commands:
                    lines.append(f"        $ {p.green(cmd)}")
            if rh.investigation_steps:
                lines.append(p.dim("      investigation steps (read-only):"))
                for cmd in rh.investigation_steps:
                    lines.append(f"        $ {p.cyan(cmd)}")
            if rh.risks:
                lines.append(p.dim("      risks:"))
                for risk in rh.risks:
                    lines.append(
                        p.yellow(
                            textwrap.fill(
                                risk,
                                width=72,
                                initial_indent="        - ",
                                subsequent_indent="          ",
                            )
                        )
                    )
    if explanation.what_i_did_not_check:
        lines.append("")
        lines.append(p.dim("What the LLM did not check:"))
        lines.append(
            p.dim(
                textwrap.fill(
                    explanation.what_i_did_not_check,
                    width=78,
                    initial_indent="  ",
                    subsequent_indent="  ",
                )
            )
        )


def _append_hypothesis(
    lines: list[str], i: int, h: Hypothesis, p: Palette
) -> None:
    lines.append("")
    lines.append(
        f"  [{i}] {p.red(h.title)}  "
        + p.dim(f"(confidence {h.confidence:.2f})")
    )
    lines.append(p.dim(f"      analyzer: {h.analyzer}"))
    if h.rationale:
        lines.append(
            textwrap.fill(
                h.rationale,
                width=72,
                initial_indent="      ",
                subsequent_indent="      ",
            )
        )
    if h.evidence:
        lines.append(p.dim("      evidence:"))
        for ev in h.evidence[:MAX_EVIDENCE_DISPLAYED]:
            lines.append(
                "        - "
                + p.dim(f"[{ev.ts.isoformat()}] {ev.kind.value}:")
                + f" {ev.summary}"
            )
        remaining = len(h.evidence) - MAX_EVIDENCE_DISPLAYED
        if remaining > 0:
            lines.append(p.dim(f"        … and {remaining} more"))
    if h.fix_commands:
        lines.append(p.dim("      suggested fix commands (NOT executed):"))
        for cmd in h.fix_commands:
            lines.append(f"        $ {p.green(cmd)}")
    if h.investigation_steps:
        label = (
            "investigation steps (no deterministic fix; LLM may suggest one):"
            if not h.fix_commands
            else "investigation steps (read-only):"
        )
        lines.append(p.dim(f"      {label}"))
        for cmd in h.investigation_steps:
            lines.append(f"        $ {p.cyan(cmd)}")
    if h.risks:
        lines.append(p.dim("      risks:"))
        for risk in h.risks:
            lines.append(
                p.yellow(
                    textwrap.fill(
                        risk,
                        width=72,
                        initial_indent="        - ",
                        subsequent_indent="          ",
                    )
                )
            )
