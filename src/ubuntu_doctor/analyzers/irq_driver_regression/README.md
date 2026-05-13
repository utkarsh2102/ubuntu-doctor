# irq_driver_regression analyzer

Detects NIC/IRQ regressions caused by package upgrades. Targets the
"cloud VM started dropping packets after unattended-upgrade" failure
mode plus its bare-metal cousins.

**Trigger events** (HARDWARE_ERROR with these markers in summary/raw):
`NIC link down`, `tx hang`, `tx timeout`, `AER`, `IRQ`, `interrupt`.
Firmware-loading errors are intentionally NOT included here — they go
to `firmware_mismatch`.

**Suspect packages, in priority order:**
1. `irqbalance` — config changes regularly cause TX hangs on busy NICs.
2. NIC drivers (`r8169`, `r8125`, `igb`, `i40e`, `ice`, `ixgbe`,
   `bnxt`, `mlx5`, `tg3`, `atlantic`).
3. Kernel images (`linux-image-*`, `linux-modules-*`).

| Correlation                                          | Confidence |
|-----------------------------------------------------|-----------:|
| NIC/IRQ error + `irqbalance` upgraded in 24h         | 0.80 |
| NIC/IRQ error + NIC-driver package upgraded in 24h   | 0.70 |
| NIC/IRQ error + kernel upgraded in 24h               | 0.70 |
| NIC/IRQ error with no correlated upgrade             | (no hypothesis) |

`fix_commands` carries a targeted downgrade + hold. Risks call out
two domain-specific nuances:

- **For kernel regressions:** boot the older kernel from GRUB
  Advanced Options first; that's non-destructive and verifies before
  committing to a package downgrade.
- **For irqbalance:** an alternative to downgrade is stopping the
  service (`systemctl stop irqbalance`) — the NIC often stabilises
  and the user can then edit `/etc/default/irqbalance` rather than
  pin the package.
