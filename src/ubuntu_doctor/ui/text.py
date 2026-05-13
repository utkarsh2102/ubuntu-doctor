"""Plain-text renderer for the human at the terminal."""

from __future__ import annotations

from ubuntu_doctor.snapshot import Hypothesis, Snapshot


def render(snapshot: Snapshot, hypotheses: list[Hypothesis]) -> str:
    lines: list[str] = []
    window = (
        f"{snapshot.window_start.isoformat()} → "
        f"{snapshot.window_end.isoformat()}"
    )
    sources = {e.source for e in snapshot.events} | set(snapshot.facts.keys())
    lines.append(f"ubuntu-doctor — window: {window}")
    lines.append(
        f"  Collected {len(snapshot.events)} events from {len(sources)} sources"
    )
    lines.append("")

    if not hypotheses:
        lines.append("No correlations found.")
        lines.append(
            "  (this means: no rule-based analyzer matched. Run with "
            "LLM enabled, or `doctor why <symptom>`, to dig deeper.)"
        )
    else:
        lines.append(f"Likely causes ({len(hypotheses)}):")
        for i, h in enumerate(hypotheses, 1):
            lines.append("")
            lines.append(f"  [{i}] {h.title}  (confidence {h.confidence:.2f})")
            lines.append(f"      analyzer: {h.analyzer}")
            if h.rationale:
                for line in _wrap(h.rationale, width=72, indent="      "):
                    lines.append(line)
            if h.evidence:
                lines.append("      evidence:")
                for ev in h.evidence:
                    lines.append(
                        f"        - [{ev.ts.isoformat()}] {ev.kind.value}: "
                        f"{ev.summary}"
                    )
            if h.commands:
                lines.append("      suggested commands (NOT executed):")
                for cmd in h.commands:
                    lines.append(f"        $ {cmd}")
            if h.risks:
                lines.append("      risks:")
                for risk in h.risks:
                    for line in _wrap(risk, width=68, indent="        - "):
                        lines.append(line)

    if snapshot.degradations:
        lines.append("")
        lines.append("What I couldn't see:")
        for deg in snapshot.degradations:
            lines.append(f"  - {deg.collector}: {deg.reason}")
            if deg.fix_command:
                lines.append(f"      to unlock: {deg.fix_command}")

    return "\n".join(lines)


def _wrap(text: str, *, width: int, indent: str) -> list[str]:
    words = text.split()
    out: list[str] = []
    current = indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            out.append(current.rstrip())
            current = indent + word
        else:
            current += (" " if current != indent else "") + word
    if current.strip():
        out.append(current.rstrip())
    return out
