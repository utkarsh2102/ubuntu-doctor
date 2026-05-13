# dmesg collector

Reads the kernel ring buffer via `journalctl --dmesg` (not the `dmesg`
binary) so we work for unprivileged users on systems with
`kernel.dmesg_restrict=1` (the Ubuntu default). Most desktop users are
in `adm` / `systemd-journal` and can read this without `sudo`.

**Command run:**
```
journalctl --dmesg --since=@<ts> --until=@<ts> -o short-iso --no-pager
```

with `LANG=C LC_ALL=C` for stable timestamp formatting.

**Classification:** kernel lines are matched against a small ordered
set of regexes that map to event kinds the downstream analyzers care
about:

- `OOM_KILL` — "Out of memory: Killed process …" or "oom-killer"
- `HARDWARE_ERROR` — firmware load failures, `regulatory.db` issues,
  MCE, PCIe AER, ATA / NVMe / USB errors, NIC link down, CPU lockups
- `KERNEL_TAINT` — taint flag set, module signature verification
  failures

Unmatched lines are intentionally **dropped**. This collector is a
classifier, not a kernel-log firehose. If a future analyzer needs raw
lines we'll add a second pathway that preserves them under `details`.

**AppArmor denials are intentionally skipped here** even though they
appear in the kernel ring buffer. The `journald` collector owns them
and carries the parsed audit fields; emitting them from both sources
would double-count and confuse the analyzer.

**Permissions:** if `journalctl --dmesg` fails (typically when the user
isn't in `adm` / `systemd-journal` *and* `dmesg_restrict=1`), the
collector emits a `DegradationReport` with `sudo journalctl --dmesg` as
the unblock hint.

**Testing:** the collector accepts a `run_command` callable so tests
can inject stub kernel output instead of touching the real journal.
