"""Deterministic re-ranker for hypotheses, biased by a user-reported symptom.

For `ubuntu-doctor why <symptom>`, the LLM normally does the re-ranking. This
module gives `--no-ai` runs (and the pre-LLM stage of normal runs) a way
to surface hypotheses related to the symptom even before the LLM weighs
in. The boost is modest (capped at +0.25) so confidences remain
trustworthy; this only reorders, it does not lie about strength.

Keyword overlap is intentionally crude: matching a single subsystem
keyword is enough to bump. We accept some false positives here because
the LLM (when enabled) will re-rank anyway.
"""

from __future__ import annotations

from dataclasses import replace

from ubuntu_doctor.snapshot import Hypothesis

SYMPTOM_SUBSYSTEMS: dict[str, tuple[str, ...]] = {
    "audio": (
        "audio",
        "sound",
        "speaker",
        "headphone",
        "mic",
        "microphone",
        "music",
    ),
    "network": (
        "wifi",
        "wireless",
        "wlan",
        "ethernet",
        "internet",
        "network",
        "ping",
        "dns",
        "vpn",
    ),
    "display": (
        "display",
        "screen",
        "monitor",
        "graphics",
        "resolution",
        "brightness",
        "flicker",
    ),
    "bluetooth": ("bluetooth", "bluez"),
    "boot": ("boot", "startup", "grub", "splash"),
    "snap": ("snap",),
    "package": ("apt", "dpkg", "package", "install", "upgrade"),
    "memory": ("oom", "out of memory", "memory", "swap"),
}

SUBSYSTEM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "audio": ("pulseaudio", "pipewire", "wireplumber", "alsa", "audio"),
    "network": (
        "networkmanager",
        "systemd-networkd",
        "systemd-resolved",
        "wpa_supplicant",
        "network-online",
        "dhcp",
        "iwd",
        "linux-firmware",
        "irqbalance",
    ),
    "display": (
        "gdm",
        "lightdm",
        "sddm",
        "display-manager",
        "xorg",
        "wayland",
    ),
    "bluetooth": ("bluez", "bluetooth"),
    "boot": ("initramfs", "plymouth", "fsck", "grub", "linux-image"),
    "snap": ("snapd", "snap"),
    "memory": ("oom", "memory"),
}

DIRECT_BOOST = 0.10
SUBSYSTEM_BOOST = 0.15
MAX_BOOST = 0.25


def _hypothesis_haystack(h: Hypothesis) -> str:
    parts: list[str] = [h.title, h.rationale]
    for event in h.evidence:
        parts.append(event.subject)
        parts.append(event.summary)
    return " ".join(parts).lower()


def _symptom_subsystems(symptom: str) -> set[str]:
    s = symptom.lower()
    matches: set[str] = set()
    for subsystem, words in SYMPTOM_SUBSYSTEMS.items():
        if any(w in s for w in words):
            matches.add(subsystem)
    return matches


def _direct_word_boost(symptom: str, haystack: str) -> float:
    for word in symptom.lower().split():
        cleaned = word.strip(".,?!\"'()")
        if len(cleaned) <= 2:
            continue
        if cleaned in haystack:
            return DIRECT_BOOST
    return 0.0


def boost_for(hypothesis: Hypothesis, symptom: str) -> float:
    haystack = _hypothesis_haystack(hypothesis)
    direct = _direct_word_boost(symptom, haystack)
    subsystem_boost = 0.0
    for subsystem in _symptom_subsystems(symptom):
        keywords = SUBSYSTEM_KEYWORDS.get(subsystem, ())
        if any(k in haystack for k in keywords):
            subsystem_boost = SUBSYSTEM_BOOST
            break
    return min(MAX_BOOST, direct + subsystem_boost)


def rank(
    hypotheses: list[Hypothesis], symptom: str | None
) -> list[Hypothesis]:
    if not symptom or not symptom.strip():
        return list(hypotheses)
    out: list[Hypothesis] = []
    for h in hypotheses:
        boost = boost_for(h, symptom)
        if boost == 0.0:
            out.append(h)
            continue
        new_conf = round(min(1.0, h.confidence + boost), 3)
        out.append(replace(h, confidence=new_conf))
    out.sort(key=lambda h: h.confidence, reverse=True)
    return out
