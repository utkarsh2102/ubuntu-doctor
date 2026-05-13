"""Computes the fingerprint of an incident — a set of canonical tokens
that capture "what kind of problem is this".

Two incidents with the same fingerprint tokens are *the same kind of
problem* and should be retrieved as few-shot examples for each other.
Tokens are picked to be stable across re-runs (no timestamps, no
numeric counts) and small in number (~5-15 typical) so Jaccard
similarity is a sensible match metric.

Tokens emitted (each prefixed with its namespace so we never collide
across types):

  - `analyzer:<id>` for every analyzer that produced a top hypothesis
  - `event:<kind>` for the distinct event kinds in those hypotheses
  - `subject:<name>` for the subjects involved (package, unit, profile)

We intentionally do NOT include free-text rationale or symptom
phrasing — those are too noisy across runs of the same underlying
issue.
"""

from __future__ import annotations

from ubuntu_doctor.snapshot import Hypothesis


def compute_fingerprint(
    hypotheses: list[Hypothesis], *, top_k: int = 5
) -> list[str]:
    if not hypotheses:
        return []
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        if tok not in seen:
            seen.add(tok)
            tokens.append(tok)

    for h in hypotheses[:top_k]:
        _add(f"analyzer:{h.analyzer}")
        for event in h.evidence:
            _add(f"event:{event.kind.value}")
            if event.subject:
                _add(f"subject:{event.subject}")
    return tokens


def jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard similarity over token sets, ignoring order and
    multiplicity."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    intersection = sa & sb
    union = sa | sb
    return len(intersection) / len(union)
