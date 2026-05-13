"""Base class for collectors.

A collector is a pure data gatherer for one source. It MUST NOT write
anything to the system. If it can't read its source (permissions, missing
file, missing tool), it returns a `DegradationReport` carrying the exact
sudo command that would unblock it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ubuntu_doctor.snapshot import DegradationReport, TimelineEvent


@dataclass
class CollectorResult:
    events: list[TimelineEvent] = field(default_factory=list)
    degradation: DegradationReport | None = None
    facts: dict | None = None


class Collector(ABC):
    id: str

    @abstractmethod
    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult: ...
