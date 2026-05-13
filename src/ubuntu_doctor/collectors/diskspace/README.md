# diskspace collector

Captures filesystem block and inode usage via `df -PT --block-size=1`
and `df -PTi`. Emits no events; populates `facts["diskspace"]` with
two lists: one per real filesystem for block usage, one for inode
usage. Pseudo-filesystems (`tmpfs`, `devtmpfs`, `squashfs`, etc.) are
filtered out — they're noise for "is /var full?" questions.

**Facts shape:**
```
facts["diskspace"] = {
    "filesystems": [
        {source, fstype, total_bytes, used_bytes, available_bytes,
         used_percent, mount}, ...
    ],
    "inodes": [
        {source, fstype, inodes_total, inodes_used, inodes_free,
         inodes_used_percent, mount}, ...
    ],
}
```

The `cache_health` analyzer consumes these to flag mounts ≥ 90% full
(disk space or inodes) and to spot a common Ubuntu failure mode: `/boot`
filling up after kernel package accumulation.
