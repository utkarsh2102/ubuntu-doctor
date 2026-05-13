# postupgrade_regression analyzer

Correlates `PACKAGE_UPGRADE` / `PACKAGE_INSTALL` events with
`SERVICE_FAILED` events that follow within 24 hours.

**Confidence model:**

- `temporal_score`: linear falloff from 1.0 at the moment of upgrade to
  0.0 at 24h.
- `name_affinity`: 0.9 for exact match, 0.6 for substring match, 0.5
  for curated kernel/firmware/driver fan-out (e.g. `linux-firmware` →
  `bluetooth`, `wpa_supplicant`, `pulseaudio`).
- Combined as `0.4 * temporal + 0.4 * affinity + 0.2 * (temporal * affinity)`,
  so a hypothesis needs both signals to clear the strong threshold.

Hypotheses below `MIN_CONFIDENCE = 0.2` are suppressed.

**Known limitation (v1):** ownership is inferred from names only.
A future iteration should consult `dpkg -S $(systemctl show <unit>
-p FragmentPath --value)` to confirm which package actually owns the
failing unit's files. That requires per-unit subprocess fan-out and
caching, and is deferred.

**Suggested commands** include rollback (`apt install pkg=oldver`),
hold (`apt-mark hold pkg`), and unit restart. They are *suggestions
only* — ubuntu-doctor never executes them.
