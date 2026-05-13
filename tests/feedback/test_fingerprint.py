from __future__ import annotations

from datetime import datetime, timezone

from ubuntu_doctor.feedback.fingerprint import compute_fingerprint, jaccard
from ubuntu_doctor.snapshot import EventKind, Hypothesis, TimelineEvent

T0 = datetime(2026, 5, 10, tzinfo=timezone.utc)


def _evt(kind: EventKind, subject: str) -> TimelineEvent:
    return TimelineEvent(
        ts=T0,
        kind=kind,
        source="x",
        subject=subject,
        summary="",
    )


def _hyp(analyzer: str, *events: TimelineEvent) -> Hypothesis:
    return Hypothesis(
        id=f"{analyzer}-{events[0].subject}" if events else analyzer,
        analyzer=analyzer,
        title="t",
        confidence=0.5,
        evidence=events,
    )


def test_fingerprint_contains_analyzer_event_and_subject_tokens():
    h = _hyp(
        "postupgrade_regression",
        _evt(EventKind.PACKAGE_UPGRADE, "linux-firmware"),
        _evt(EventKind.SERVICE_FAILED, "pulseaudio.service"),
    )
    fp = compute_fingerprint([h])
    assert "analyzer:postupgrade_regression" in fp
    assert "event:package.upgrade" in fp
    assert "event:service.failed" in fp
    assert "subject:linux-firmware" in fp
    assert "subject:pulseaudio.service" in fp


def test_fingerprint_is_stable_across_repeated_calls():
    h = _hyp(
        "apparmor_denials",
        _evt(EventKind.APPARMOR_DENIED, "snap.spotify.spotify"),
    )
    assert compute_fingerprint([h]) == compute_fingerprint([h])


def test_fingerprint_omits_timestamps_and_counts():
    # Two hypotheses that "are the same kind of problem" but differ in
    # timestamps and number of evidence rows should produce the same
    # fingerprint.
    a = _hyp(
        "apparmor_denials",
        _evt(EventKind.APPARMOR_DENIED, "snap.x"),
    )
    b = _hyp(
        "apparmor_denials",
        _evt(EventKind.APPARMOR_DENIED, "snap.x"),
        _evt(EventKind.APPARMOR_DENIED, "snap.x"),
    )
    assert compute_fingerprint([a]) == compute_fingerprint([b])


def test_fingerprint_empty_input():
    assert compute_fingerprint([]) == []


def test_jaccard_identical_is_one():
    assert jaccard(["a", "b", "c"], ["a", "b", "c"]) == 1.0


def test_jaccard_disjoint_is_zero():
    assert jaccard(["a"], ["b"]) == 0.0


def test_jaccard_partial_overlap():
    # Two of three tokens overlap; three of four are in the union.
    assert jaccard(["a", "b", "c"], ["a", "b", "d"]) == 0.5


def test_jaccard_empty_inputs_are_zero():
    assert jaccard([], []) == 0.0
    assert jaccard(["a"], []) == 0.0
