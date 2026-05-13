# apparmor_audit collector

Reads `/var/log/audit/audit.log` (and `.1` rotated) directly, parses
`apparmor="DENIED"` lines, and emits `APPARMOR_DENIED` events.

**Why this exists alongside `journald`.** The journald collector
captures apparmor denials via `journalctl --grep`, which works
everywhere. But on auditd-enabled servers, journald typically retains
audit messages for only a few hours by default — `/var/log/audit/audit.log`
keeps weeks. This collector buys deeper history on those systems.

**Deduplication:** the `apparmor_denials` analyzer dedups events by
`(profile, ts, name)`, so simultaneous activity from both collectors
doesn't double-count hypotheses.

**Permissions:**
- File doesn't exist (typical desktop without auditd) → returns
  zero events, no degradation. Silent and correct.
- File exists but isn't readable → `DegradationReport` with
  `sudo cat /var/log/audit/audit.log` as the unblock hint.

The audit-line format is stable across distros:
`type=AVC msg=audit(<epoch>.<frac>:<serial>): apparmor="DENIED" ...`
with `key="value"` pairs for the rest. The same regex parses both the
classic quoted form and the newer unquoted `apparmor=DENIED`.
