# snap_refresh_breakage analyzer

Pairs `SNAP_REFRESH` events (refresh/install/revert/connect/etc. for
a snap) with subsequent `APPARMOR_DENIED` events on the same snap's
profile within 24h.

| Combination                                                     | Confidence |
|----------------------------------------------------------------|-----------:|
| Refresh + ≥1 apparmor denial on `snap.<name>.*` in 24h          | 0.80 |
| Refresh that ended in `Error`/`Undone` (no denial)              | 0.70 |
| Clean refresh, no denial                                         | (no hypothesis) |

The default `fix_commands` is `sudo snap revert <name>` — a known-safe
rollback. Risks call out two things that matter:

- Revert undoes whatever security improvements were in the new
  revision; check `snap info` for `notes` first.
- Auto-refresh will re-bring-back the broken version unless the user
  uses `snap refresh --hold` while reporting the bug.

When AppArmor denials are present, an extra risk line points out that
the *real* fix is often `snap connect <name>:<interface>` rather than
revert — the denial's `name` field (a filesystem path) hints at which
interface (`audio-playback`, `home`, `removable-media`, etc.) is the
right one. The LLM is well-placed to make that call.
