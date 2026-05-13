"""Detects network/IRQ regressions caused by package upgrades.

Targets the cloud-VM and bare-metal cases where `irqbalance`, a NIC
driver kernel module package, or the kernel itself was upgraded and
network errors started appearing in dmesg shortly after.

Trigger events (NIC link drops, ATA/NVMe driver errors aren't included
here — they go to firmware_mismatch). We look for these
HARDWARE_ERROR summaries: `NIC link down`, `Intel NIC TX hang`,
`PCIe AER error`. They're orthogonal to firmware load failures.

Suspect packages:
  - `irqbalance` — changes IRQ affinity, can starve NICs
  - `linux-image-*`, `linux-modules-*` — new kernel modules
  - any package matching common NIC driver prefixes (`r8169`, `igb`,
    `i40e`, `ixgbe`, `bnxt`, `mlx5`, `nvme`)

Confidence:
  - NIC/IRQ error + `irqbalance` upgraded in 24h               → 0.80
  - NIC/IRQ error + kernel upgraded in 24h                     → 0.70
  - NIC/IRQ error + NIC-driver-named package upgraded in 24h   → 0.70
  - Otherwise no hypothesis (avoid noisy generic hardware reports)
"""

from __future__ import annotations

from datetime import timedelta

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

CORRELATION_WINDOW = timedelta(hours=24)

# Substrings that mark a hardware error as IRQ/NIC-related (as opposed
# to firmware-loading, which firmware_mismatch handles).
NIC_ERROR_MARKERS = (
    "nic link down",
    "tx hang",
    "tx timeout",
    "aer",
    "irq",
    "interrupt",
)

# Package categories. Order matters: irqbalance has the highest signal,
# kernel images second, named drivers third.
NIC_DRIVER_PREFIXES = (
    "r8169",
    "r8125",
    "igb",
    "i40e",
    "ice",
    "ixgbe",
    "bnxt",
    "mlx5",
    "tg3",
    "atlantic",
)


def _is_nic_irq_error(e: TimelineEvent) -> bool:
    if e.kind != EventKind.HARDWARE_ERROR:
        return False
    text = " ".join(
        [
            (e.summary or "").lower(),
            (e.details.get("raw") or "").lower(),
        ]
    )
    return any(marker in text for marker in NIC_ERROR_MARKERS)


def _categorise_upgrade(pkg: str) -> str:
    pkg = pkg.lower()
    if pkg == "irqbalance":
        return "irqbalance"
    if pkg.startswith("linux-image-") or pkg.startswith("linux-modules-"):
        return "kernel"
    if any(p in pkg for p in NIC_DRIVER_PREFIXES):
        return "nic-driver"
    return ""


def _find_culprit(
    errors: list[TimelineEvent], upgrades: list[TimelineEvent]
) -> tuple[TimelineEvent | None, str]:
    """Return the highest-priority upgrade preceding the earliest
    error within the correlation window."""
    if not errors or not upgrades:
        return None, ""
    earliest_error = min(e.ts for e in errors)
    candidates: dict[str, TimelineEvent] = {}
    for u in upgrades:
        delta = earliest_error - u.ts
        if delta < timedelta(0) or delta > CORRELATION_WINDOW:
            continue
        category = _categorise_upgrade(u.subject)
        if not category:
            continue
        if category not in candidates or abs(u.ts - earliest_error) < abs(
            candidates[category].ts - earliest_error
        ):
            candidates[category] = u
    for category in ("irqbalance", "nic-driver", "kernel"):
        if category in candidates:
            return candidates[category], category
    return None, ""


def _confidence(category: str) -> float:
    if category == "irqbalance":
        return 0.80
    if category in {"nic-driver", "kernel"}:
        return 0.70
    return 0.0


def _hypothesis(
    errors: list[TimelineEvent],
    upgrade: TimelineEvent,
    category: str,
) -> Hypothesis:
    old = upgrade.details.get("old_version", "?")
    new = upgrade.details.get("new_version", "?")
    rationale_parts = [
        f"dmesg shows {len(errors)} NIC/IRQ-related hardware "
        f"error(s); the most recent is `{errors[-1].summary}` at "
        f"{errors[-1].ts.isoformat()}. "
    ]
    if category == "irqbalance":
        rationale_parts.append(
            f"`irqbalance` was upgraded ({old} → {new}) at "
            f"{upgrade.ts.isoformat()}. `irqbalance` config changes "
            "frequently cause IRQ-affinity regressions that manifest "
            "as TX hangs or dropped packets on busy NICs — this is the "
            "classic 'cloud VM started dropping packets after "
            "unattended-upgrade' failure mode."
        )
    elif category == "kernel":
        rationale_parts.append(
            f"`{upgrade.subject}` (a kernel package) was upgraded "
            f"({old} → {new}) at {upgrade.ts.isoformat()}, ahead of "
            "the errors. New kernel modules sometimes regress on "
            "specific NICs; the older kernel is still bootable via "
            "GRUB."
        )
    else:
        rationale_parts.append(
            f"`{upgrade.subject}` (a NIC driver package) was upgraded "
            f"({old} → {new}) at {upgrade.ts.isoformat()}. NIC "
            "driver upgrades occasionally regress on specific "
            "device generations."
        )

    fix_commands = (
        f"sudo apt install {upgrade.subject}={old}",
        f"sudo apt-mark hold {upgrade.subject}",
    )
    investigation_steps = [
        "dmesg --ctime | grep -iE 'nic|irq|tx hang|tx timeout|aer'",
        "ip -s link",
        "cat /proc/interrupts | grep -i eth",
    ]
    if category == "irqbalance":
        investigation_steps.extend(
            [
                "systemctl status irqbalance",
                "cat /etc/default/irqbalance",
            ]
        )
    elif category == "kernel":
        investigation_steps.append(
            "dpkg -l 'linux-image-*' | grep '^ii'"
        )

    risks: list[str] = [
        f"Downgrading `{upgrade.subject}` may pin a version with known "
        "issues. Hold only as long as you need to confirm the diagnosis."
    ]
    if category == "kernel":
        risks.append(
            "Try booting the older kernel via GRUB advanced options "
            "first; that's non-destructive and verifies the regression "
            "before committing to a package downgrade."
        )
    if category == "irqbalance":
        risks.append(
            "An alternative to downgrade: stop `irqbalance` and verify "
            "the NIC stabilises (`sudo systemctl stop irqbalance`). "
            "If it does, the new config is the cause; you can then "
            "edit `/etc/default/irqbalance` rather than downgrade."
        )

    return Hypothesis(
        id=f"irq-{upgrade.subject}-{category}-{len(errors)}",
        analyzer="irq_driver_regression",
        title=(
            f"NIC/IRQ errors after `{upgrade.subject}` upgrade ({category})"
        ),
        confidence=_confidence(category),
        rationale="".join(rationale_parts),
        evidence=(upgrade,) + tuple(errors[-5:]),
        fix_commands=fix_commands,
        investigation_steps=tuple(investigation_steps),
        risks=tuple(risks),
    )


class IrqDriverRegressionAnalyzer(Analyzer):
    id = "irq_driver_regression"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        errors = [e for e in snapshot.events if _is_nic_irq_error(e)]
        if not errors:
            return []
        upgrades = [
            e
            for e in snapshot.events
            if e.kind
            in (EventKind.PACKAGE_UPGRADE, EventKind.PACKAGE_INSTALL)
        ]
        upgrade, category = _find_culprit(errors, upgrades)
        if upgrade is None:
            # No correlated suspect package. Don't emit a noisy
            # generic "your network had errors" hypothesis — leave
            # that to the LLM if the user asks about networking.
            return []
        return [_hypothesis(errors, upgrade, category)]


ANALYZER = IrqDriverRegressionAnalyzer()
