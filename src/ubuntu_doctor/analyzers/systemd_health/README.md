# systemd_health analyzer

Pure systemd-side health check. Reads `SERVICE_FAILED` events from the
snapshot (produced by the `systemd_failed` collector) and classifies
each by what systemd itself reports — `Result` and `LoadState` — into a
result-specific hypothesis with tailored investigation commands.

**Result-type playbook (confidence in parens):**

| Result        | Confidence | Direction                                          |
|---------------|-----------:|----------------------------------------------------|
| `oom-kill`    | 0.70       | Memory pressure — investigate leak before raising limits |
| `core-dump`   | 0.60       | Fatal signal + core file — `coredumpctl info`      |
| `timeout`     | 0.55       | Startup/shutdown exceeded TimeoutSec — find the slow dependency |
| `signal`      | 0.45       | Killed by external signal (OOM, watchdog, sibling) |
| `exit-code` / unknown | 0.30 | Generic failed-state fallback                    |

**LoadState playbook:** if a unit's `LoadState` is `not-found`,
`bad-setting`, or `masked`, that takes precedence over the result type
and emits a unit-file hypothesis (confidence 0.55) pointing at
`systemctl cat`, `dpkg --audit`, and `systemctl show -p LoadError`.

**Subsystem clusters:** when 2+ failed units fall into one of the known
subsystems (`network`, `audio`, `display`, `boot`), the analyzer adds a
single cluster hypothesis with subsystem-specific investigation
commands. Confidence is `min(0.9, 0.65 + 0.05 × len(units))` — failing
together is a stronger signal than any one unit alone.

**Relationship to `postupgrade_regression`:** intentionally orthogonal.
This analyzer never looks at package events. If both analyzers fire on
the same unit, both hypotheses are emitted and the ranker/LLM layer
reconciles them. The two angles ("what changed recently?" vs "what does
systemd think went wrong?") rarely agree by accident, so seeing both is
useful signal.

**Suggested commands** are investigation commands, not fixes.
ubuntu-doctor never executes them.
