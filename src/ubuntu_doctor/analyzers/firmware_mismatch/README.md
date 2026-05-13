# firmware_mismatch analyzer

Correlates dmesg firmware/hardware errors with recent `linux-firmware`
or `linux-image-*` upgrades and uses the hardware inventory to surface
the specific PCI/USB vendor:device pair likely affected.

| Correlation                                                  | Confidence |
|-------------------------------------------------------------|-----------:|
| firmware error + `linux-firmware*` upgraded in 48h           | 0.80 |
| firmware error + `linux-image-*` upgraded in 48h             | 0.70 |
| firmware error with no upgrade correlation                   | 0.45 |

**Fix commands** (when an upgrade is correlated) include the targeted
rollback (`apt install <pkg>=<old_version>`) plus a hold to prevent
re-upgrade. Risks call out two important nuances:

- A linux-firmware rollback can expose a security issue the upgrade
  was addressing. The risk text suggests checking the changelog first.
- For kernel-correlated regressions, booting an older kernel via GRUB
  is non-destructive — try that before downgrading the package.

The hardware inventory match is best-effort substring matching against
device descriptions. Real diagnostics often need the LLM to interpret
the firmware filename + the matched device.
