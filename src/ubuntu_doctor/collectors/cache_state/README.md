# cache_state collector

Inspects the apt/dpkg cache and lock state on the local filesystem.
Emits no events; populates `facts["cache_state"]` with structured data
the `cache_health` analyzer consumes.

**What it looks at:**
- `/var/cache/apt/archives/partial/` — non-empty means an interrupted
  download.
- `/var/cache/apt/archives/` — total `.deb` cache size.
- `/var/lib/dpkg/lock`, `/var/lib/dpkg/lock-frontend` — existence, age,
  and size. A non-zero-byte lock or one held for hours often blocks installs.
- `/var/lib/apt/lists/` — newest mtime, so we know how long since
  `apt update`.
- `/var/crash/` — number of apport reports.

**Facts shape:**
```
facts["cache_state"] = {
    "apt_partial_count":    int,
    "apt_partial_packages": [filename, ...] (capped at 20),
    "apt_archives_bytes":   int,
    "apt_lists_mtime":      float | None,
    "apt_lists_age_seconds": float | None,
    "dpkg_lock":            {path, age_seconds, size_bytes} | None,
    "dpkg_lock_frontend":   {path, age_seconds, size_bytes} | None,
    "crash_count":          int,
}
```

All filesystem reads are best-effort: any `FileNotFoundError`/`PermissionError`
yields a zero/empty value rather than degrading the whole run.
