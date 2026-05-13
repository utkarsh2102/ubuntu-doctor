# apt_log collector

Captures apt transaction history and current package state. Emits **no
events** — populates `facts["apt_log"]` for the `held_packages` and
`cache_health` analyzers to consume.

**Sources:**
- `/var/log/apt/history.log[.1.gz]` — `Start-Date`/`End-Date` blocks
  with `Commandline`/`Requested-By` and per-action lists. Higher-level
  than dpkg.log: the user's *intent*, not just dpkg's actions.
- `apt-mark showhold` — packages held back from upgrades.
- `dpkg --audit` — packages in an inconsistent state.
- `/var/log/apt/term.log[.1.gz]` — recent error markers, capped at 20.

**Why no events?** dpkg_history is authoritative for `PACKAGE_*` events.
Emitting them here would double-count without adding correlation
signal. The interesting things apt_log adds — commandline,
held/broken state, term.log errors — are state facts, not events.

**Facts shape:**
```
facts["apt_log"] = {
    "recent_transactions": [
        {start_ts, end_ts, commandline, requested_by,
         counts: {install, upgrade, remove, ...}},
        ...
    ],
    "held_packages":    ["nvidia-driver-470", ...],
    "broken_packages":  ["libfoo-dev", ...],
    "term_log_errors":  ["E: Sub-process /usr/bin/dpkg returned ...", ...],
}
```
