"""Types returned by the LLM layer.

`LLMUnavailable` is the single exception callers must catch when they
want to degrade to deterministic-only output. Anything else — network
failure, malformed response, schema mismatch — is normalised into this.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankedHypothesis:
    hypothesis_id: str
    title: str
    why: str
    confidence: float
    commands: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


@dataclass(frozen=True)
class LLMExplanation:
    summary: str
    ranked_hypotheses: tuple[RankedHypothesis, ...]
    what_i_did_not_check: str
    model: str
    prompt_version: str = ""
    raw_response: str = ""


class LLMUnavailable(RuntimeError):
    """The LLM endpoint was unreachable, errored, or emitted unparseable output.

    Callers should catch this and degrade to deterministic output.
    """
