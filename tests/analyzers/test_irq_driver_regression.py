from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.irq_driver_regression.plugin import (
    IrqDriverRegressionAnalyzer,
)
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 13, 14, 30, tzinfo=timezone.utc)


def _nic_error(raw: str = "nic link is down on eth0", ts: datetime | None = None) -> TimelineEvent:
    return TimelineEvent(
        ts=ts or T0,
        kind=EventKind.HARDWARE_ERROR,
        source="dmesg",
        subject="nic",
        summary="NIC link down",
        details={"raw": raw, "captures": {}},
    )


def _upgrade(pkg: str, ts: datetime, old: str = "1.0", new: str = "1.1") -> TimelineEvent:
    return TimelineEvent(
        ts=ts,
        kind=EventKind.PACKAGE_UPGRADE,
        source="dpkg_history",
        subject=pkg,
        summary=f"{pkg} upgraded",
        details={"old_version": old, "new_version": new},
    )


def _snap(events: list[TimelineEvent]) -> Snapshot:
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=14),
        window_end=T0 + timedelta(days=1),
        events=sorted(events, key=lambda e: e.ts),
    )


async def test_no_nic_errors_yields_no_hypotheses():
    assert await IrqDriverRegressionAnalyzer().analyze(_snap([])) == []


async def test_nic_error_without_correlated_upgrade_is_silent():
    snap = _snap([_nic_error()])
    # No correlated suspect package → no noisy hypothesis.
    assert await IrqDriverRegressionAnalyzer().analyze(snap) == []


async def test_irqbalance_upgrade_is_top_priority():
    snap = _snap(
        [
            _nic_error(ts=T0),
            _upgrade("irqbalance", T0 - timedelta(hours=1)),
            _upgrade("linux-image-6.8.0-50-generic", T0 - timedelta(hours=2)),
        ]
    )
    h = (await IrqDriverRegressionAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.80
    assert "irqbalance" in h.title
    # Risks must surface the non-destructive `systemctl stop irqbalance`
    # check.
    risk_text = " ".join(h.risks)
    assert "systemctl stop irqbalance" in risk_text


async def test_kernel_upgrade_correlation():
    snap = _snap(
        [
            _nic_error(),
            _upgrade("linux-image-6.8.0-50-generic", T0 - timedelta(hours=2)),
        ]
    )
    h = (await IrqDriverRegressionAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.70
    risk_text = " ".join(h.risks)
    assert "GRUB" in risk_text


async def test_nic_driver_package_correlation():
    snap = _snap(
        [
            _nic_error("Intel NIC TX hang on i40e"),
            _upgrade("linux-modules-i40e", T0 - timedelta(hours=1)),
        ]
    )
    hs = await IrqDriverRegressionAnalyzer().analyze(snap)
    # The package name includes both `linux-` (kernel-ish) and `i40e`
    # (driver). The analyzer's category preference is irqbalance >
    # nic-driver > kernel; with linux-image-* the kernel branch fires.
    # Confidence is 0.70 either way for non-irqbalance correlation.
    assert len(hs) == 1
    assert hs[0].confidence == 0.70


async def test_error_preceding_upgrade_does_not_correlate():
    snap = _snap(
        [
            _nic_error(ts=T0),
            _upgrade("irqbalance", T0 + timedelta(hours=1)),
        ]
    )
    assert await IrqDriverRegressionAnalyzer().analyze(snap) == []


async def test_upgrade_outside_window_does_not_correlate():
    snap = _snap(
        [
            _nic_error(ts=T0),
            _upgrade("irqbalance", T0 - timedelta(days=10)),
        ]
    )
    assert await IrqDriverRegressionAnalyzer().analyze(snap) == []
