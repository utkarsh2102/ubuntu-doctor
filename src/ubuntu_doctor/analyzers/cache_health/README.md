# cache_health analyzer

Consumes `cache_state` and `diskspace` facts (and `dpkg_history`
events for the /boot-kernel correlation). Emits one hypothesis per
detected health issue:

| Detection                                                          | Confidence |
|--------------------------------------------------------------------|-----------:|
| `/var/cache/apt/archives/partial/` non-empty                       | 0.70 |
| Stale dpkg lock (non-zero size or held > 1h with size)             | 0.65 |
| Critical mount (`/`, `/boot`, `/var`, `/home`, `/usr`) ≥ 90% full  | 0.75 |
| Any mount ≥ 98% full                                                | 0.85 |
| Non-critical mount ≥ 95% full                                       | 0.60 |
| Inode usage ≥ 90%                                                   | 0.70 |
| apt lists older than 30 days                                        | 0.45 |
| `/boot` ≥ 80% full **and** linux-image installed in window          | 0.80 |

Where a concrete fix exists, `fix_commands` carries it
(`apt clean`, `apt autoremove --purge`, `journalctl --vacuum-time`,
etc.) plus targeted risk warnings. For ambiguous cases (stale locks,
generic inode exhaustion) `fix_commands` is empty and the LLM (or the
user) decides based on the investigation steps.

The analyzer NEVER auto-suggests deleting dpkg locks blindly — that
would corrupt the package database if a real apt process is holding
them. Investigation steps point at `lsof` first.
