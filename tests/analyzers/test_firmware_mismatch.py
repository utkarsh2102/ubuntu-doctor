from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ubuntu_doctor.analyzers.firmware_mismatch.plugin import (
    FirmwareMismatchAnalyzer,
)
from ubuntu_doctor.snapshot import EventKind, Snapshot, TimelineEvent

T0 = datetime(2026, 5, 13, 14, 30, tzinfo=timezone.utc)


def _firmware_error(raw: str = "firmware: failed to load rtl_nic/rtl8125a-3.fw") -> TimelineEvent:
    return TimelineEvent(
        ts=T0,
        kind=EventKind.HARDWARE_ERROR,
        source="dmesg",
        subject="firmware",
        summary="firmware load failure",
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


def _snap(
    events: list[TimelineEvent], hardware_facts: dict | None = None
) -> Snapshot:
    return Snapshot(
        started_at=T0,
        window_start=T0 - timedelta(days=14),
        window_end=T0 + timedelta(days=1),
        events=sorted(events, key=lambda e: e.ts),
        facts={"hardware": hardware_facts} if hardware_facts else {},
    )


async def test_no_firmware_errors_yields_no_hypotheses():
    assert await FirmwareMismatchAnalyzer().analyze(_snap([])) == []


async def test_firmware_upgrade_correlation_is_highest_confidence():
    snap = _snap(
        [
            _firmware_error(),
            _upgrade("linux-firmware", T0 - timedelta(hours=2)),
        ]
    )
    h = (await FirmwareMismatchAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.80
    assert any("apt install linux-firmware=" in c for c in h.fix_commands)
    assert any("apt-mark hold linux-firmware" in c for c in h.fix_commands)


async def test_kernel_upgrade_correlation_is_medium_confidence():
    snap = _snap(
        [
            _firmware_error(),
            _upgrade("linux-image-6.8.0-50-generic", T0 - timedelta(hours=3)),
        ]
    )
    h = (await FirmwareMismatchAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.70
    # Risk text must steer the user toward booting an older kernel first.
    risk_text = " ".join(h.risks)
    assert "GRUB" in risk_text or "older kernel" in risk_text


async def test_uncorrelated_firmware_error_is_low_confidence():
    snap = _snap([_firmware_error()])
    h = (await FirmwareMismatchAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.45
    assert h.fix_commands == ()


async def test_upgrade_after_error_is_not_correlated():
    snap = _snap(
        [
            _firmware_error(),
            _upgrade("linux-firmware", T0 + timedelta(hours=1)),
        ]
    )
    h = (await FirmwareMismatchAnalyzer().analyze(snap))[0]
    assert h.confidence == 0.45  # error preceded upgrade, no correlation


async def test_hardware_match_appended_to_rationale():
    snap = _snap(
        [
            _firmware_error("firmware: failed to load rtl_nic/rtl8125a-3.fw"),
            _upgrade("linux-firmware", T0 - timedelta(hours=2)),
        ],
        hardware_facts={
            "pci_devices": [
                {
                    "vendor": "10ec",
                    "device": "8125",
                    "description": "RTL8125 2.5GbE Controller",
                    "class": "Network controller",
                }
            ],
            "usb_devices": [],
        },
    )
    h = (await FirmwareMismatchAnalyzer().analyze(snap))[0]
    assert "10ec:8125" in h.rationale or "RTL8125" in h.rationale
