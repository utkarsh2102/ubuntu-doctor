# dpkg_history collector

Parses `/var/log/dpkg.log` and `/var/log/dpkg.log.1` into structured
package install/upgrade/remove/purge events. Lines outside the requested
time window are dropped at parse time.

**Source files:** `/var/log/dpkg.log`, `/var/log/dpkg.log.1`, optionally
gzipped rotations (`*.log.2.gz`, ...). The `.gz` paths are not used by
default — pass them via `DpkgHistoryCollector(log_paths=...)` if you
need a window > ~30 days.

**Permissions:** `/var/log/dpkg.log` is world-readable on standard
Ubuntu installs. If it isn't, we emit a `DegradationReport` with
`sudo cat /var/log/dpkg.log` as the unblock hint and continue.

**Output events:** `package.install`, `package.upgrade`,
`package.remove`, `package.purge`. Other action lines
(`status`, `configure`, `startup`, `trigproc`) are intentionally
discarded — they're correlation noise.
