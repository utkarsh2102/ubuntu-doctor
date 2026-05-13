"""systemd-only health check.

For every currently-failed unit in the snapshot, classify it by what
systemd itself reports — `Result` (oom-kill / core-dump / timeout /
signal / exit-code / ...) and `LoadState` (loaded / masked / not-found /
bad-setting) — and emit a hypothesis tuned to that classification.

When two or more failed units belong to the same subsystem (network,
audio, display, boot), additionally emit a cluster hypothesis: the
underlying subsystem is the more likely root cause than any individual
unit.

This analyzer is orthogonal to `postupgrade_regression`: it does not
correlate failures with recent package changes. If both fire on the
same unit, the LLM/ranker layer reconciles them. Surfacing both angles
is intentional during v1 so users see what each lens contributes.
"""

from __future__ import annotations

from collections import defaultdict

from ubuntu_doctor.analyzers.base import Analyzer
from ubuntu_doctor.snapshot import EventKind, Hypothesis, Snapshot, TimelineEvent

# Subsystem → substring patterns matched against the unit name (lowercased).
# Two or more units in the same subsystem trigger a cluster hypothesis.
_SUBSYSTEMS: dict[str, tuple[str, ...]] = {
    "network": (
        "networkmanager",
        "systemd-networkd",
        "systemd-resolved",
        "wpa_supplicant",
        "network-online",
        "dhcp",
        "iwd",
    ),
    "audio": ("pulseaudio", "pipewire", "wireplumber", "alsa"),
    "display": (
        "gdm",
        "lightdm",
        "sddm",
        "display-manager",
        "xorg",
        "wayland",
    ),
    "boot": ("initrd", "initramfs", "plymouth", "fsck"),
}


def _subsystem_for(unit: str) -> str | None:
    base = unit.lower()
    for name, patterns in _SUBSYSTEMS.items():
        if any(p in base for p in patterns):
            return name
    return None


def _hypothesis_id(prefix: str, *parts: str) -> str:
    return prefix + "-" + "-".join(parts)


def _classify(failure: TimelineEvent) -> Hypothesis:
    unit = failure.subject
    result = (failure.details.get("result") or "").strip()
    load_state = (failure.details.get("load_state") or "").strip()
    ts_iso = failure.ts.isoformat()

    if load_state == "masked":
        return Hypothesis(
            id=_hypothesis_id("systemd-loadstate", unit, load_state),
            analyzer="systemd_health",
            title=f"{unit}: unit is masked",
            confidence=0.55,
            rationale=(
                f"systemd reports LoadState=masked for {unit}. Someone "
                "(possibly a package postinst) masked the unit so it "
                "can't start. Decide whether that masking was intentional "
                "before un-masking."
            ),
            evidence=(failure,),
            fix_commands=(),
            investigation_steps=(
                f"systemctl cat {unit}",
                f"systemctl status {unit}",
                f"ls -l /etc/systemd/system/{unit} /run/systemd/system/{unit} 2>/dev/null",
            ),
            risks=(
                "Unmasking with `sudo systemctl unmask` re-enables a unit "
                "the operator may have disabled deliberately. Confirm the "
                "reason it was masked before reversing it.",
            ),
        )

    if load_state in {"not-found", "bad-setting"}:
        return Hypothesis(
            id=_hypothesis_id("systemd-loadstate", unit, load_state),
            analyzer="systemd_health",
            title=f"{unit}: unit file is {load_state}",
            confidence=0.55,
            rationale=(
                f"systemd reports LoadState={load_state} for {unit}. "
                "This usually means the owning package is partially "
                "installed or the unit file has a syntax error. Finishing "
                "the interrupted dpkg run and re-reading unit files is "
                "the standard fix."
            ),
            evidence=(failure,),
            fix_commands=(
                "sudo dpkg --configure -a",
                "sudo systemctl daemon-reload",
            ),
            investigation_steps=(
                f"systemctl cat {unit}",
                f"systemctl show {unit} -p FragmentPath,LoadError",
                "sudo dpkg --audit",
            ),
            risks=(
                "`dpkg --configure -a` will finish whatever package "
                "install was previously interrupted. If you stopped that "
                "install on purpose, run `dpkg --audit` first to see "
                "what would resume.",
            ),
        )

    # The remaining branches (`oom-kill`, `core-dump`, `timeout`,
    # `signal`, generic failures) do not have a deterministic fix that
    # the analyzer can safely propose without more context. Surface
    # investigation steps; let the LLM produce a tailored fix when it
    # has the full denial/log detail.

    if result == "oom-kill":
        return Hypothesis(
            id=_hypothesis_id("systemd-oom", unit, ts_iso),
            analyzer="systemd_health",
            title=f"{unit} was killed by the OOM killer",
            confidence=0.7,
            rationale=(
                f"systemd reports Result=oom-kill for {unit}: the kernel "
                "killed the service under memory pressure. The root "
                "cause is either a leak in the service itself or "
                "system-wide memory exhaustion from another process. "
                "The right fix depends on which — investigate first."
            ),
            evidence=(failure,),
            fix_commands=(),
            investigation_steps=(
                f"journalctl -u {unit} -p err -b --no-pager",
                "dmesg --ctime | grep -i 'out of memory'",
                f"systemctl show {unit} -p "
                "MemoryAccounting,MemoryMax,MemoryHigh,MemoryCurrent",
            ),
            risks=(
                "Don't just raise MemoryMax: that masks a leak and lets "
                "the offending process starve other services.",
            ),
        )

    if result == "core-dump":
        return Hypothesis(
            id=_hypothesis_id("systemd-coredump", unit, ts_iso),
            analyzer="systemd_health",
            title=f"{unit} crashed (core dump)",
            confidence=0.6,
            rationale=(
                f"systemd reports Result=core-dump for {unit}: a fatal "
                "signal terminated the service and the kernel produced a "
                "core file. Likely a software bug, memory corruption, or "
                "a missing/incompatible shared library. Inspect the core "
                "dump before deciding on a fix."
            ),
            evidence=(failure,),
            fix_commands=(),
            investigation_steps=(
                f"coredumpctl info {unit}",
                f"journalctl -u {unit} -p err -b --no-pager",
                "ls -lt /var/crash/",
            ),
            risks=(),
        )

    if result == "timeout":
        return Hypothesis(
            id=_hypothesis_id("systemd-timeout", unit, ts_iso),
            analyzer="systemd_health",
            title=f"{unit} timed out during start or stop",
            confidence=0.55,
            rationale=(
                f"systemd reports Result=timeout for {unit}: it exceeded "
                "its configured TimeoutStartSec/TimeoutStopSec. Common "
                "causes are a slow dependency, a network mount that "
                "isn't ready, or a bug in the service itself."
            ),
            evidence=(failure,),
            fix_commands=(),
            investigation_steps=(
                f"systemctl status {unit}",
                f"journalctl -u {unit} -b --no-pager",
                f"systemctl show {unit} -p "
                "TimeoutStartUSec,TimeoutStopUSec,After,Requires,Wants",
            ),
            risks=(
                "Bumping TimeoutStartSec masks the symptom; check why "
                "the dependency or work is slow first.",
            ),
        )

    if result == "signal":
        return Hypothesis(
            id=_hypothesis_id("systemd-signal", unit, ts_iso),
            analyzer="systemd_health",
            title=f"{unit} was killed by a signal",
            confidence=0.45,
            rationale=(
                f"systemd reports Result=signal for {unit}: a fatal "
                "signal was delivered. Could be the kernel (OOM, "
                "watchdog), another process, or the service crashing "
                "into SIGABRT/SIGSEGV without producing a core dump."
            ),
            evidence=(failure,),
            fix_commands=(),
            investigation_steps=(
                f"journalctl -u {unit} -p err -b --no-pager",
                "dmesg --ctime | grep -iE 'killed|out of memory|watchdog'",
                f"systemctl show {unit} -p "
                "WatchdogUSec,WatchdogTimestamp,Restart",
            ),
            risks=(),
        )

    return Hypothesis(
        id=_hypothesis_id("systemd-failed", unit, ts_iso),
        analyzer="systemd_health",
        title=f"{unit} is in failed state ({result or 'unknown'})",
        confidence=0.3,
        rationale=(
            f"{unit} is currently failed. systemd's classification is "
            f"`{result or 'unknown'}`. Inspect the unit's own logs to "
            "determine the cause; the LLM may suggest a fix from the "
            "full context."
        ),
        evidence=(failure,),
        fix_commands=(),
        investigation_steps=(
            f"systemctl status {unit}",
            f"journalctl -u {unit} -p err -b --no-pager",
        ),
        risks=(),
    )


def _cluster_hypothesis(
    subsystem: str, failures: list[TimelineEvent]
) -> Hypothesis:
    unit_names = sorted(f.subject for f in failures)
    confidence = round(min(0.9, 0.65 + 0.05 * len(failures)), 3)
    investigation_steps: tuple[str, ...]
    if subsystem == "network":
        investigation_steps = (
            "ip -brief addr",
            "ip route",
            "systemctl --no-pager status NetworkManager systemd-networkd systemd-resolved 2>/dev/null",
            "journalctl -b -p err -u NetworkManager -u systemd-networkd -u systemd-resolved --no-pager",
        )
    elif subsystem == "audio":
        investigation_steps = (
            "systemctl --user status pipewire pipewire-pulse wireplumber 2>/dev/null",
            "journalctl --user -b -p err -u pipewire -u wireplumber --no-pager 2>/dev/null",
            "lsmod | grep -iE 'snd|audio'",
        )
    elif subsystem == "display":
        investigation_steps = (
            "systemctl status display-manager",
            "journalctl -b -p err -u gdm -u lightdm -u sddm --no-pager 2>/dev/null",
            "ls -lt /var/log/Xorg.*.log 2>/dev/null",
        )
    elif subsystem == "boot":
        investigation_steps = (
            "journalctl -b 0 -p err --no-pager",
            "ls /boot",
            "df -h /boot",
        )
    else:
        investigation_steps = tuple(
            f"systemctl status {u}" for u in unit_names
        )

    return Hypothesis(
        id=_hypothesis_id("systemd-cluster", subsystem, *unit_names),
        analyzer="systemd_health",
        title=(
            f"{len(failures)} {subsystem} units failed together: "
            f"{', '.join(unit_names)}"
        ),
        confidence=confidence,
        rationale=(
            f"Several {subsystem}-related systemd units are currently in "
            f"failed state ({', '.join(unit_names)}). When multiple "
            "units in one subsystem fail at once, the cause usually sits "
            "one layer below them — the subsystem itself — rather than "
            "each unit independently. Investigate the shared dependency "
            "before the individual services."
        ),
        evidence=tuple(failures),
        fix_commands=(),
        investigation_steps=investigation_steps,
        risks=(),
    )


class SystemdHealthAnalyzer(Analyzer):
    id = "systemd_health"

    async def analyze(self, snapshot: Snapshot) -> list[Hypothesis]:
        failures = [
            e for e in snapshot.events if e.kind == EventKind.SERVICE_FAILED
        ]
        if not failures:
            return []

        hypotheses: list[Hypothesis] = [_classify(f) for f in failures]

        by_subsystem: dict[str, list[TimelineEvent]] = defaultdict(list)
        for f in failures:
            sub = _subsystem_for(f.subject)
            if sub:
                by_subsystem[sub].append(f)
        for subsystem, group in by_subsystem.items():
            if len(group) >= 2:
                hypotheses.append(_cluster_hypothesis(subsystem, group))

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses


ANALYZER = SystemdHealthAnalyzer()
