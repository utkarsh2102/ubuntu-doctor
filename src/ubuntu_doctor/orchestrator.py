"""Runs collectors in parallel, merges results into a Snapshot, and runs
analyzers in parallel against it."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Iterable

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.collectors.base import Collector, CollectorResult
from ubuntu_doctor.snapshot import DegradationReport, Hypothesis, Snapshot


async def _safe_collect(
    collector: Collector, window_start: datetime, window_end: datetime
) -> CollectorResult:
    # Intentional broad catch: this is the exception barrier between one
    # collector and the rest of the run. A failure in one source must not
    # blank out everything else; it must be surfaced as a degradation.
    try:
        return await collector.collect(window_start, window_end)
    except Exception as exc:  # noqa: BLE001
        return CollectorResult(
            events=[],
            degradation=DegradationReport(
                collector=collector.id,
                reason=f"collector raised {type(exc).__name__}: {exc}",
                fix_command=None,
            ),
        )


async def build_snapshot(
    collectors: Iterable[Collector],
    window_start: datetime,
    window_end: datetime,
) -> Snapshot:
    collectors = list(collectors)
    results = await asyncio.gather(
        *(_safe_collect(c, window_start, window_end) for c in collectors)
    )
    snapshot = Snapshot(
        started_at=datetime.now(timezone.utc),
        window_start=window_start,
        window_end=window_end,
    )
    for collector, result in zip(collectors, results, strict=True):
        snapshot.events.extend(result.events)
        if result.degradation is not None:
            snapshot.degradations.append(result.degradation)
        if result.facts is not None:
            snapshot.facts[collector.id] = result.facts
    snapshot.events.sort(key=lambda e: e.ts)
    return snapshot


async def run_analyzers(
    analyzers: Iterable[Analyzer], snapshot: Snapshot
) -> list[Hypothesis]:
    results = await asyncio.gather(*(a.analyze(snapshot) for a in analyzers))
    hypotheses: list[Hypothesis] = []
    for batch in results:
        hypotheses.extend(batch)
    hypotheses.sort(key=lambda h: h.confidence, reverse=True)
    return hypotheses
