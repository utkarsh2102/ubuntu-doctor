# systemd_failed collector

Lists currently-failed systemd units and stamps each event with its last
exit time.

**Commands run:**
- `systemctl --failed --no-legend --plain --no-pager`
- `systemctl show <unit> --property=ActiveExitTimestamp,Result,LoadState,Description --no-pager`

Both invoked with `LANG=C LC_ALL=C` for a stable timestamp format
(`Sat 2026-05-10 14:30:22 UTC`).

**Permissions:** runs unprivileged for system units the user can see
(everything for system-wide D-Bus access). User-scoped units (those
managed by `systemctl --user`) are out of scope for v1.

**Output events:** one `SERVICE_FAILED` per failed unit, timestamped at
the parsed `ActiveExitTimestamp`. If the timestamp can't be parsed, the
event is stamped at the collector's run time and a
`details.timestamp_parsed=False` flag is set so analyzers know not to
trust the temporal alignment.

**Testing:** the collector accepts a `run_command` callable so tests can
inject stub `systemctl` output instead of touching the real system.
