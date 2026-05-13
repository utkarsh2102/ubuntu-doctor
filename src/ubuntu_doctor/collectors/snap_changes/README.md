# snap_changes collector

Captures snap operations (refresh / install / remove / connect /
disconnect / enable / disable / revert) via `snap changes --abs-time`.

**Events:** one `SNAP_REFRESH` per completed change inside the window.
`details["operation"]` carries the specific verb (`refresh`, `install`,
`remove`, `connect`, …). `details["status"]` carries `Done` / `Error` /
`Undone` so analyzers can distinguish successful refreshes from failed
ones.

**Facts:** `facts["snap_changes"]["installed"]` from `snap list` and
`facts["snap_changes"]["connections"]` from `snap connections`.

**Permissions:** all three commands work unprivileged.

**Behaviour when snap isn't installed:** if the `snap` binary isn't on
PATH the collector returns zero events plus a `DegradationReport`.
Systems without snapd (some servers / minimal containers) are common
enough that this is a graceful path, not an error.

The `snap_refresh_breakage` analyzer pairs these events with AppArmor
denials on `snap.<name>.*` profiles within ~24h to surface the classic
"snap stopped working after auto-refresh" failure mode.
