# journald collector

Reads the systemd journal via `journalctl` and emits `APPARMOR_DENIED`
events.

**Why scoped to AppArmor denials in v1?** Pulling the whole journal
would blow the LLM token budget and is mostly noise for diagnosis.
AppArmor denials are the high-signal class that has no other natural
collector and that the `apparmor_denials` analyzer needs. Other
journal-derived event types (service restarts, generic crash patterns,
oneshot failures) will land as new filtered queries when an analyzer
needs them.

**Command run:**
```
journalctl --since=@<ts> --until=@<ts> -o json --no-pager \
           --grep 'apparmor=("DENIED"|DENIED)'
```

The regex catches both classic `apparmor="DENIED"` (quoted) and the
newer kaudit `apparmor=DENIED` (unquoted) header formats.

**Output events:** one `APPARMOR_DENIED` per matched journal entry,
with `details` populated from the audit message:

- `profile` (the AppArmor profile that fired, e.g. `snap.spotify.spotify`)
- `operation` (e.g. `open`, `exec`, `capable`, `mknod`)
- `name` (the path or resource that was denied)
- `comm` (the calling executable)
- `pid`
- `requested_mask` and `denied_mask`
- `transport` (`audit` if auditd is on, `kernel` otherwise)
- `raw` (the full original message for the LLM and the renderer)

The event's `subject` is the profile name so analyzers can group on it.

**Permissions:** members of `adm` / `systemd-journal` see the full
journal. If the user is in neither, this collector returns zero events
(no real failure mode — they just don't have access). The degradation
hint points at `usermod -aG systemd-journal`.

**Testing:** the collector accepts a `run_command` callable so tests
can inject stub JSON output instead of touching the real journal.
