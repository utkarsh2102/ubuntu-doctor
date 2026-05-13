from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ubuntu_doctor.feedback.store import Incident, IncidentStore


def test_save_and_get_roundtrip(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    incident = Incident(
        fingerprint=["analyzer:a", "event:x", "subject:y"],
        chosen_hypothesis_ids=["h1"],
        suggested_fix_commands=["sudo dpkg --configure -a"],
        applied_commands=["sudo dpkg --configure -a"],
        observed_effect="package install finished",
        outcome="fixed",
        notes="went smoothly",
    )
    saved_id = store.save(incident)
    assert saved_id is not None

    fetched = store.get(saved_id)
    assert fetched is not None
    assert fetched.fingerprint == incident.fingerprint
    assert fetched.outcome == "fixed"
    assert fetched.applied_commands == incident.applied_commands
    assert fetched.observed_effect == "package install finished"
    assert fetched.ts is not None


def test_save_rejects_unknown_outcome(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    with pytest.raises(ValueError):
        store.save(Incident(fingerprint=["x"], outcome="banana"))


def test_find_similar_returns_matches_above_threshold(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    store.save(
        Incident(
            fingerprint=["analyzer:a", "subject:linux-firmware", "event:upg"],
            outcome="fixed",
            applied_commands=["apt install linux-firmware=1.0"],
        )
    )
    store.save(
        Incident(
            fingerprint=["analyzer:b", "subject:unrelated"],
            outcome="fixed",
        )
    )
    # Same shape as the first → should match it, not the second.
    results = store.find_similar(
        ["analyzer:a", "subject:linux-firmware", "event:upg"]
    )
    assert len(results) == 1
    assert results[0].applied_commands == ["apt install linux-firmware=1.0"]
    assert results[0].similarity == 1.0


def test_find_similar_respects_min_similarity(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    store.save(
        Incident(
            fingerprint=["analyzer:a", "subject:x", "event:e"],
            outcome="fixed",
        )
    )
    # Only one token overlaps; Jaccard = 1/5 = 0.2 — borderline.
    matches = store.find_similar(
        ["analyzer:a", "other:1", "other:2", "other:3"],
        min_similarity=0.3,
    )
    assert matches == []


def test_find_similar_sorts_by_similarity_then_recency(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save(
        Incident(
            fingerprint=["a", "b"],
            outcome="fixed",
            ts=base,
        )
    )
    store.save(
        Incident(
            fingerprint=["a", "b", "c"],
            outcome="fixed",
            ts=base + timedelta(days=1),
        )
    )
    store.save(
        Incident(
            fingerprint=["a", "b"],
            outcome="fixed",
            ts=base + timedelta(days=2),
        )
    )
    results = store.find_similar(["a", "b"])
    assert len(results) == 3
    # First two have similarity 1.0; the more recent should come first.
    assert results[0].similarity == 1.0
    assert results[0].ts > results[1].ts
    # Third has lower similarity (2/3) and so comes last.
    assert results[2].similarity == pytest.approx(2 / 3)


def test_find_similar_top_k_cap(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    for i in range(5):
        store.save(
            Incident(
                fingerprint=["a", "b"], outcome="fixed", notes=f"#{i}"
            )
        )
    results = store.find_similar(["a", "b"], top_k=2)
    assert len(results) == 2


def test_find_similar_empty_fingerprint_returns_nothing(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    store.save(Incident(fingerprint=["a"], outcome="fixed"))
    assert store.find_similar([]) == []


def test_all_returns_newest_first(tmp_path: Path):
    store = IncidentStore(tmp_path / "db.sqlite")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save(Incident(fingerprint=["x"], outcome="fixed", ts=base))
    store.save(
        Incident(
            fingerprint=["y"],
            outcome="fixed",
            ts=base + timedelta(days=1),
        )
    )
    all_rows = store.all()
    assert [r.fingerprint for r in all_rows] == [["y"], ["x"]]
