from __future__ import annotations

from datetime import datetime, timezone

from ubuntu_doctor.ranker import boost_for, rank
from ubuntu_doctor.snapshot import EventKind, Hypothesis, TimelineEvent

T0 = datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)


def _event(subject: str, summary: str | None = None) -> TimelineEvent:
    return TimelineEvent(
        ts=T0,
        kind=EventKind.SERVICE_FAILED,
        source="systemd_failed",
        subject=subject,
        summary=summary or subject,
    )


def _hyp(
    id_: str,
    title: str,
    confidence: float,
    *,
    evidence_subjects: list[str] | None = None,
) -> Hypothesis:
    evidence = tuple(_event(s) for s in (evidence_subjects or []))
    return Hypothesis(
        id=id_,
        analyzer="test",
        title=title,
        confidence=confidence,
        rationale="",
        evidence=evidence,
    )


def test_no_symptom_returns_input_unchanged():
    hs = [
        _hyp("a", "irrelevant", 0.5),
        _hyp("b", "also irrelevant", 0.3),
    ]
    assert rank(hs, None) == hs
    assert rank(hs, "") == hs
    assert rank(hs, "   ") == hs


def test_audio_symptom_boosts_pulseaudio_hypothesis():
    pulse = _hyp(
        "a",
        "pulseaudio.service failed after upgrade",
        0.5,
        evidence_subjects=["pulseaudio.service"],
    )
    other = _hyp("b", "htop crashed", 0.55)
    ranked = rank([other, pulse], "no audio")
    assert ranked[0].id == "a"
    assert ranked[0].confidence > pulse.confidence
    assert ranked[1].confidence == other.confidence


def test_wifi_symptom_boosts_networkmanager_hypothesis():
    nm = _hyp(
        "a",
        "NetworkManager.service failed",
        0.4,
        evidence_subjects=["NetworkManager.service"],
    )
    audio = _hyp("b", "audio thing", 0.45)
    ranked = rank([audio, nm], "wifi keeps dropping")
    assert ranked[0].id == "a"


def test_boost_is_capped_at_max():
    # Even with both direct word match and subsystem affinity, total boost
    # must not exceed MAX_BOOST = 0.25.
    h = _hyp(
        "a",
        "pulseaudio audio sound issue",
        0.7,
        evidence_subjects=["pulseaudio.service"],
    )
    b = boost_for(h, "no audio sound")
    assert b <= 0.25 + 1e-9


def test_confidence_never_exceeds_one():
    h = _hyp(
        "a",
        "pulseaudio failed",
        0.9,
        evidence_subjects=["pulseaudio.service"],
    )
    ranked = rank([h], "no audio")
    assert ranked[0].confidence <= 1.0


def test_unknown_symptom_words_give_no_boost():
    h = _hyp("a", "some failure", 0.5)
    assert boost_for(h, "blorbleflarg") == 0.0
    ranked = rank([h], "blorbleflarg")
    assert ranked[0].confidence == h.confidence


def test_short_words_are_ignored_for_direct_match():
    # Common short tokens like "in", "on", "is" shouldn't count as a
    # direct word match — they'd boost almost everything.
    h = _hyp("a", "this is something", 0.5)
    assert boost_for(h, "is on in") == 0.0
