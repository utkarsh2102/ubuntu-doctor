"""Base class for analyzers.

An analyzer consumes a `Snapshot` and emits zero or more `Hypothesis`
objects. Analyzers are rule-based — the LLM does not invent hypotheses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ubuntu_doctor.snapshot import Hypothesis, Snapshot


class Analyzer(ABC):
    id: str

    @abstractmethod
    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]: ...
