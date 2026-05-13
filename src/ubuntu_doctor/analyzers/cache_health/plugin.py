"""Detects cache / dpkg / disk health issues that silently break installs.

Surfaces (each as a separate hypothesis):

  - Interrupted downloads in `/var/cache/apt/archives/partial/`.
  - Stale dpkg locks (non-zero size or held > 1h).
  - Disk space ≥ 90% on critical mounts (`/`, `/boot`, `/var`,
    `/home`).
  - Disk space ≥ 95% anywhere else.
  - Inode usage ≥ 90% on any real filesystem.
  - apt list metadata older than 30 days.
  - `/boot` getting full while linux-image packages are accumulating —
    classic Ubuntu kernel-cleanup case.

Each case has a concrete fix command when one exists. Generic "out of
space" without context is left for the user / LLM to triage further
since the right cleanup depends on what's on the disk.
"""

from __future__ import annotations

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot

CRITICAL_MOUNTS = ("/", "/boot", "/var", "/home", "/usr")
STALE_LOCK_SECONDS = 3600           # > 1h
STALE_APT_LISTS_SECONDS = 30 * 24 * 3600  # > 30 days
DISK_WARN_CRITICAL = 90
DISK_WARN_OTHER = 95
INODE_WARN = 90


def _partial_downloads(facts: dict) -> Hypothesis | None:
    count = facts.get("apt_partial_count", 0)
    if count <= 0:
        return None
    packages = ", ".join(facts.get("apt_partial_packages") or [])
    return Hypothesis(
        id=f"cache-partial-{count}",
        analyzer="cache_health",
        title=(
            f"{count} interrupted package download(s) in "
            "`/var/cache/apt/archives/partial/`"
        ),
        confidence=0.7,
        rationale=(
            f"`/var/cache/apt/archives/partial/` contains {count} "
            f"file(s) ({packages}). These are leftovers from an "
            "interrupted `apt` run. They take disk space and can "
            "occasionally trick apt into thinking a package is "
            "already downloaded."
        ),
        evidence=(),
        fix_commands=("sudo apt clean",),
        investigation_steps=(
            "ls -la /var/cache/apt/archives/partial/",
        ),
        risks=(
            "`apt clean` removes ALL cached .debs, not just partials. "
            "Subsequent installs will re-download. Use `apt autoclean` "
            "instead if you want to keep current versions cached.",
        ),
    )


def _stale_lock(lock: dict | None, label: str) -> Hypothesis | None:
    if lock is None:
        return None
    age = lock.get("age_seconds", 0.0)
    size = lock.get("size_bytes", 0)
    if size == 0 and age < STALE_LOCK_SECONDS:
        return None
    if size == 0:
        # Empty lock that's just been around a while is normal. Only
        # complain about non-zero ones.
        return None
    path = lock.get("path", "")
    return Hypothesis(
        id=f"cache-stale-lock-{label}",
        analyzer="cache_health",
        title=f"dpkg lock `{path}` looks stale ({int(age)}s old, {size} bytes)",
        confidence=0.65,
        rationale=(
            f"`{path}` has size {size} bytes and was last touched "
            f"{int(age)}s ago. Non-zero dpkg lock files usually mean "
            "a dpkg/apt process is currently holding it. If no such "
            "process exists (`ps aux | grep -E 'apt|dpkg'`), it's "
            "stale and is blocking new installs."
        ),
        evidence=(),
        fix_commands=(),
        investigation_steps=(
            "ps aux | grep -E 'apt|dpkg' | grep -v grep",
            f"sudo lsof {path}",
        ),
        risks=(
            f"Do NOT delete `{path}` while an apt/dpkg process is "
            "running — you will corrupt the package database. Only "
            "remove the lock if `lsof` reports no holder.",
        ),
    )


def _disk_pressure(facts: dict) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    for fs in facts.get("filesystems") or []:
        mount = fs.get("mount", "")
        pcent = fs.get("used_percent", 0)
        is_critical = mount in CRITICAL_MOUNTS
        threshold = DISK_WARN_CRITICAL if is_critical else DISK_WARN_OTHER
        if pcent < threshold:
            continue
        total_gb = fs.get("total_bytes", 0) / (1024**3)
        avail_gb = fs.get("available_bytes", 0) / (1024**3)
        confidence = 0.85 if pcent >= 98 else (0.75 if is_critical else 0.6)
        commands = []
        risks = ["Always check what's on the filesystem before deleting."]
        if mount == "/boot":
            commands.append("sudo apt autoremove --purge")
            risks.append(
                "`autoremove --purge` removes orphaned packages, including "
                "older kernels. Keep at least the previous-working kernel."
            )
        elif mount == "/var" or mount == "/":
            commands.append("sudo apt clean && sudo apt autoclean")
            commands.append("sudo journalctl --vacuum-time=30d")
        out.append(
            Hypothesis(
                id=f"cache-disk-{mount.replace('/', '_')}-{pcent}",
                analyzer="cache_health",
                title=(
                    f"Filesystem `{mount}` is {pcent}% full "
                    f"({avail_gb:.1f} GB free of {total_gb:.1f} GB)"
                ),
                confidence=confidence,
                rationale=(
                    f"Mount `{mount}` reports {pcent}% used. "
                    + (
                        "This is a critical system mount; running out "
                        "here typically prevents `apt` from completing, "
                        "blocks logging, or causes services to fail."
                        if is_critical
                        else "Capacity pressure at this level often "
                        "causes secondary failures in services that "
                        "log or cache to it."
                    )
                ),
                evidence=(),
                fix_commands=tuple(commands),
                investigation_steps=(
                    f"sudo du -h --max-depth=1 {mount} | sort -h | tail -20",
                    "df -h",
                ),
                risks=tuple(risks),
            )
        )
    return out


def _inode_pressure(facts: dict) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    for fs in facts.get("inodes") or []:
        pcent = fs.get("inodes_used_percent", 0)
        if pcent < INODE_WARN:
            continue
        mount = fs.get("mount", "")
        out.append(
            Hypothesis(
                id=f"cache-inodes-{mount.replace('/', '_')}-{pcent}",
                analyzer="cache_health",
                title=f"Filesystem `{mount}` is {pcent}% inode-full",
                confidence=0.7,
                rationale=(
                    f"`{mount}` has {pcent}% of inodes used. The "
                    "filesystem may still have free space, but it "
                    "can't create new files. This often manifests as "
                    "`No space left on device` even when `df -h` "
                    "shows plenty of free GB."
                ),
                evidence=(),
                fix_commands=(),
                investigation_steps=(
                    f"sudo find {mount} -xdev -type f | "
                    "awk -F/ '{print $2\"/\"$3}' | sort | uniq -c | "
                    "sort -nr | head",
                ),
                risks=(
                    "Most inode exhaustion is caused by huge directories "
                    "of small files (caches, mail spools). Identify the "
                    "directory before deleting anything.",
                ),
            )
        )
    return out


def _stale_apt_lists(facts: dict) -> Hypothesis | None:
    age = facts.get("apt_lists_age_seconds")
    if age is None or age < STALE_APT_LISTS_SECONDS:
        return None
    days = int(age // (24 * 3600))
    return Hypothesis(
        id=f"cache-stale-lists-{days}",
        analyzer="cache_health",
        title=f"apt package lists are {days} days old",
        confidence=0.45,
        rationale=(
            f"`/var/lib/apt/lists/` was last updated {days} days ago. "
            "You may see stale 'Hash Sum mismatch' errors or miss "
            "security updates until `apt update` runs again. This is "
            "informational unless you're seeing install failures."
        ),
        evidence=(),
        fix_commands=("sudo apt update",),
        investigation_steps=(),
        risks=(),
    )


def _boot_kernel_pressure(
    cache_facts: dict, disk_facts: dict, snapshot: Snapshot
) -> Hypothesis | None:
    """`/boot` filling up while linux-image packages were installed
    recently — the classic kernel-accumulation case."""
    boot_pcent = 0
    for fs in disk_facts.get("filesystems") or []:
        if fs.get("mount") == "/boot":
            boot_pcent = fs.get("used_percent", 0)
            break
    if boot_pcent < 80:
        return None
    linux_image_installs = [
        e
        for e in snapshot.events
        if e.kind
        in (EventKind.PACKAGE_INSTALL, EventKind.PACKAGE_UPGRADE)
        and e.subject.startswith("linux-image-")
    ]
    if not linux_image_installs:
        return None
    return Hypothesis(
        id=f"cache-boot-kernel-{boot_pcent}",
        analyzer="cache_health",
        title=(
            f"/boot is {boot_pcent}% full and {len(linux_image_installs)} "
            "kernel(s) were installed in window"
        ),
        confidence=0.8,
        rationale=(
            f"`/boot` is {boot_pcent}% full and "
            f"{len(linux_image_installs)} `linux-image-*` package(s) "
            "were installed in the analysis window. Ubuntu keeps "
            "the current and previous kernel by default, but if "
            "`unattended-upgrades` or other tooling has been adding "
            "kernels without removing old ones, /boot can fill up and "
            "block the next kernel upgrade or even apt itself."
        ),
        evidence=tuple(linux_image_installs[:5]),
        fix_commands=("sudo apt autoremove --purge",),
        investigation_steps=(
            "dpkg -l 'linux-image-*' | grep '^ii'",
            "ls -la /boot",
        ),
        risks=(
            "`autoremove --purge` removes orphaned packages including "
            "older kernels. KEEP at least the previously-working kernel "
            "as a fallback (the running kernel is never removed).",
        ),
    )


class CacheHealthAnalyzer(Analyzer):
    id = "cache_health"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        cache_facts = snapshot.facts.get("cache_state", {})
        disk_facts = snapshot.facts.get("diskspace", {})

        hypotheses: list[Hypothesis] = []
        h = _partial_downloads(cache_facts)
        if h is not None:
            hypotheses.append(h)
        h = _stale_lock(cache_facts.get("dpkg_lock"), "main")
        if h is not None:
            hypotheses.append(h)
        h = _stale_lock(cache_facts.get("dpkg_lock_frontend"), "frontend")
        if h is not None:
            hypotheses.append(h)
        hypotheses.extend(_disk_pressure(disk_facts))
        hypotheses.extend(_inode_pressure(disk_facts))
        h = _stale_apt_lists(cache_facts)
        if h is not None:
            hypotheses.append(h)
        h = _boot_kernel_pressure(cache_facts, disk_facts, snapshot)
        if h is not None:
            hypotheses.append(h)
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses


ANALYZER = CacheHealthAnalyzer()
