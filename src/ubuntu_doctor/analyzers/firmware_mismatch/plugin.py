"""Correlates dmesg firmware/hardware errors with recent
`linux-firmware` / `linux-image-*` upgrades and the hardware
inventory.

The classic case: user runs `apt upgrade`, `linux-firmware` is updated,
reboot, Wi-Fi or Bluetooth stops working with a "firmware: failed to
load …" line in dmesg. The hardware inventory tells us which PCI/USB
device IDs are affected so the LLM (and any bug report) has the
specific vendor:device pair.

Confidence:
  - dmesg firmware error AND `linux-firmware` upgraded in window     → 0.80
  - dmesg firmware error AND `linux-image-*` upgraded in window      → 0.70
  - dmesg firmware error with no obvious upgrade correlation         → 0.45
"""

from __future__ import annotations

from datetime import timedelta

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

CORRELATION_WINDOW = timedelta(hours=48)
FIRMWARE_PACKAGES = ("linux-firmware", "intel-microcode", "amd64-microcode")
KERNEL_PREFIXES = ("linux-image-", "linux-modules-", "linux-headers-")


def _is_firmware_error(e: TimelineEvent) -> bool:
    if e.kind != EventKind.HARDWARE_ERROR:
        return False
    summary = (e.summary or "").lower()
    return (
        "firmware" in summary
        or "regulatory" in summary
        or "module signature" in summary
    )


def _firmware_keyword(error: TimelineEvent) -> str:
    """Extract a likely firmware filename or device hint from the
    raw dmesg body. E.g. `rtl_nic/rtl8125a-3.fw` from
    `firmware: failed to load rtl_nic/rtl8125a-3.fw (-2)`."""
    raw = (error.details.get("raw") or "").lower()
    for token in raw.split():
        token = token.strip(",.()")
        if token.endswith(".fw") or "/" in token and "firmware" not in token:
            return token
    return ""


def _matching_hardware(
    hardware_facts: dict, firmware_hint: str
) -> list[dict]:
    """Return PCI + USB devices whose description plausibly relates to
    the firmware hint. This is a cheap substring match — the LLM will
    use these hints alongside the raw dmesg to confirm."""
    if not firmware_hint:
        return []
    tokens = {
        t.lower()
        for t in firmware_hint.replace("/", "_").replace("-", "_").split("_")
        if len(t) >= 3
    }
    matches: list[dict] = []
    for device in (hardware_facts.get("pci_devices") or []) + (
        hardware_facts.get("usb_devices") or []
    ):
        description = (device.get("description") or "").lower()
        class_name = (device.get("class") or "").lower()
        if any(t in description or t in class_name for t in tokens):
            matches.append(device)
    return matches[:5]


def _recent_upgrades(
    snapshot: Snapshot, since: timedelta = CORRELATION_WINDOW
) -> list[TimelineEvent]:
    """All in-window PACKAGE_UPGRADE/INSTALL events sorted by ts."""
    return [
        e
        for e in snapshot.events
        if e.kind in (EventKind.PACKAGE_UPGRADE, EventKind.PACKAGE_INSTALL)
    ]


def _find_upgrade(
    errors: list[TimelineEvent], upgrades: list[TimelineEvent]
) -> tuple[TimelineEvent | None, str]:
    """Find a firmware-related upgrade close in time to any of the
    errors. Returns the upgrade event and a classification:
    `"firmware"`, `"kernel"`, or `""` if no correlation."""
    if not errors or not upgrades:
        return None, ""
    earliest_error = min(e.ts for e in errors)
    firmware: list[TimelineEvent] = []
    kernel: list[TimelineEvent] = []
    for u in upgrades:
        delta = earliest_error - u.ts
        # Upgrade must precede the error and be within the window.
        if delta < timedelta(0) or delta > CORRELATION_WINDOW:
            continue
        pkg = u.subject.lower()
        if any(p in pkg for p in FIRMWARE_PACKAGES):
            firmware.append(u)
        elif any(pkg.startswith(p) for p in KERNEL_PREFIXES):
            kernel.append(u)
    if firmware:
        return min(firmware, key=lambda u: abs(u.ts - earliest_error)), "firmware"
    if kernel:
        return min(kernel, key=lambda u: abs(u.ts - earliest_error)), "kernel"
    return None, ""


def _hypothesis(
    errors: list[TimelineEvent],
    upgrade: TimelineEvent | None,
    classification: str,
    hardware_matches: list[dict],
) -> Hypothesis:
    if classification == "firmware":
        confidence = 0.80
    elif classification == "kernel":
        confidence = 0.70
    else:
        confidence = 0.45

    rationale_parts = [
        f"dmesg reports {len(errors)} firmware/hardware error(s) — the "
        "most recent is "
        f"`{errors[-1].summary}` at {errors[-1].ts.isoformat()}. "
    ]
    if upgrade is not None:
        old = upgrade.details.get("old_version", "?")
        new = upgrade.details.get("new_version", "?")
        rationale_parts.append(
            f"`{upgrade.subject}` was upgraded ({old} → {new}) at "
            f"{upgrade.ts.isoformat()}, which precedes the error. "
        )
        if classification == "firmware":
            rationale_parts.append(
                "A bad linux-firmware upgrade is one of the most "
                "common causes of post-update Wi-Fi/Bluetooth regressions. "
                "Rolling back is usually the fastest verification."
            )
        else:
            rationale_parts.append(
                "A new kernel ships with a matching modules tree; if "
                "your hardware needs firmware loaded by a specific "
                "kernel module, a kernel upgrade can expose firmware "
                "incompatibilities that the old kernel hid."
            )
    else:
        rationale_parts.append(
            "No matching firmware/kernel upgrade is visible in the "
            "window. This could be intermittent hardware, a kernel "
            "regression that the snapshot's `--since` doesn't reach, "
            "or a firmware/hardware mismatch from initial install."
        )
    if hardware_matches:
        device_lines = "; ".join(
            f"{d.get('vendor')}:{d.get('device') or d.get('product')} "
            f"({d.get('description')})"
            for d in hardware_matches
        )
        rationale_parts.append(
            f" Likely affected hardware: {device_lines}."
        )

    fix_commands: tuple[str, ...] = ()
    investigation_steps: list[str] = [
        "dmesg --ctime | grep -iE 'firmware|regulatory|module signature'",
        "dpkg -l 'linux-firmware*' 'linux-image-*' | grep '^ii'",
    ]
    if upgrade is not None:
        old = upgrade.details.get("old_version", "?")
        fix_commands = (
            f"sudo apt install {upgrade.subject}={old}",
            f"sudo apt-mark hold {upgrade.subject}",
        )
        investigation_steps.append(
            f"apt-cache policy {upgrade.subject}"
        )

    risks: list[str] = []
    if upgrade is not None:
        risks.append(
            f"Downgrading `{upgrade.subject}` may pin you on a version "
            "with known issues; check launchpad and changelogs before "
            "holding long-term."
        )
        if classification == "firmware":
            risks.append(
                "If linux-firmware was upgraded for a security advisory "
                "(check the changelog), rolling back exposes that issue. "
                "Prefer the targeted-snapshot approach: install just "
                "the specific firmware blob's previous version if your "
                "device firmware is shipped as a separate file."
            )
        if classification == "kernel":
            risks.append(
                "Booting an older kernel via GRUB is non-destructive "
                "and usually faster than downgrading the package. Try "
                "that first; only roll back the package if the older "
                "kernel boots cleanly."
            )

    return Hypothesis(
        id=(
            f"firmware-{(upgrade.subject if upgrade else 'unattributed')}-"
            f"{len(errors)}"
        ),
        analyzer="firmware_mismatch",
        title=(
            "Firmware/hardware error after recent "
            f"{classification} upgrade"
            if upgrade
            else "Firmware/hardware error with no clear upgrade correlation"
        ),
        confidence=confidence,
        rationale="".join(rationale_parts),
        evidence=tuple(errors[-5:])
        + ((upgrade,) if upgrade is not None else ()),
        fix_commands=fix_commands,
        investigation_steps=tuple(investigation_steps),
        risks=tuple(risks),
    )


class FirmwareMismatchAnalyzer(Analyzer):
    id = "firmware_mismatch"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        errors = [e for e in snapshot.events if _is_firmware_error(e)]
        if not errors:
            return []
        upgrades = _recent_upgrades(snapshot)
        upgrade, classification = _find_upgrade(errors, upgrades)
        hardware_matches: list[dict] = []
        hardware_facts = snapshot.facts.get("hardware", {})
        if hardware_facts:
            firmware_hint = _firmware_keyword(errors[-1])
            hardware_matches = _matching_hardware(
                hardware_facts, firmware_hint
            )
        return [_hypothesis(errors, upgrade, classification, hardware_matches)]


ANALYZER = FirmwareMismatchAnalyzer()
