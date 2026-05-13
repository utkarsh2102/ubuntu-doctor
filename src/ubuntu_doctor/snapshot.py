"""Core data types passed between collectors, analyzers, and renderers.

A `Snapshot` is the entire input to the analysis layer: a chronologically
sorted list of `TimelineEvent`s plus per-collector facts and degradation
reports. Analyzers consume a `Snapshot` and emit `Hypothesis` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EventKind(str, Enum):
    PACKAGE_INSTALL = "package.install"
    PACKAGE_UPGRADE = "package.upgrade"
    PACKAGE_REMOVE = "package.remove"
    PACKAGE_PURGE = "package.purge"
    SERVICE_FAILED = "service.failed"
    SERVICE_RESTART = "service.restart"
    KERNEL_TAINT = "kernel.taint"
    APPARMOR_DENIED = "apparmor.denied"
    SNAP_REFRESH = "snap.refresh"
    OOM_KILL = "oom.kill"
    HARDWARE_ERROR = "hardware.error"


@dataclass(frozen=True)
class TimelineEvent:
    """One observed event with enough structure for analyzers to correlate."""

    ts: datetime
    kind: EventKind
    source: str
    subject: str
    summary: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DegradationReport:
    """Emitted by a collector that couldn't read its source fully.

    `fix_command` is the exact command the user can run to unblock — never
    silently omit data without telling the user how to get it back.
    """

    collector: str
    reason: str
    fix_command: str | None = None


@dataclass
class Snapshot:
    started_at: datetime
    window_start: datetime
    window_end: datetime
    events: list[TimelineEvent] = field(default_factory=list)
    degradations: list[DegradationReport] = field(default_factory=list)
    facts: dict[str, dict] = field(default_factory=dict)

    def events_in_window(
        self, since: datetime, until: datetime
    ) -> list[TimelineEvent]:
        return [e for e in self.events if since <= e.ts <= until]


@dataclass(frozen=True)
class Hypothesis:
    """One analyzer's guess at a cause, with evidence and suggested actions.

    Suggested actions split by intent so the user (and the LLM) never
    confuse "look at this" with "do this":

      - `fix_commands` — concrete repair commands the analyzer is
        confident address the root cause. Example: rolling back a bad
        upgrade, finishing an interrupted dpkg run.
      - `investigation_steps` — read-only commands to gather more info
        when the deterministic analyzer can't produce a confident fix.
        Example: `journalctl -u <unit>`, `aa-status`.

    Both are *suggested* text — never executed by ubuntu-doctor.
    """

    id: str
    analyzer: str
    title: str
    confidence: float
    rationale: str = ""
    evidence: tuple[TimelineEvent, ...] = ()
    fix_commands: tuple[str, ...] = ()
    investigation_steps: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
