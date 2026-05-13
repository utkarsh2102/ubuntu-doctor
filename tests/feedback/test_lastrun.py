from __future__ import annotations

from pathlib import Path

from ubuntu_doctor.feedback.lastrun import (
    LastRun,
    LastRunCache,
    LastRunHypothesis,
)


def test_write_and_read_roundtrip(tmp_path: Path):
    cache = LastRunCache(tmp_path / "last_run.json")
    run = LastRun(
        ts="2026-05-13T13:00:00+00:00",
        symptom="audio gone",
        fingerprint=["analyzer:postupgrade_regression", "subject:pulseaudio"],
        hypotheses=[
            LastRunHypothesis(
                id="hyp-1",
                title="hypothesis title",
                analyzer="postupgrade_regression",
                fix_commands=["sudo apt install foo=1.0"],
            )
        ],
    )
    cache.write(run)

    loaded = cache.read()
    assert loaded is not None
    assert loaded.symptom == "audio gone"
    assert loaded.fingerprint == run.fingerprint
    assert len(loaded.hypotheses) == 1
    assert loaded.hypotheses[0].id == "hyp-1"
    assert loaded.hypotheses[0].fix_commands == [
        "sudo apt install foo=1.0"
    ]


def test_read_missing_file_returns_none(tmp_path: Path):
    cache = LastRunCache(tmp_path / "absent.json")
    assert cache.read() is None


def test_write_overwrites_previous_run(tmp_path: Path):
    cache = LastRunCache(tmp_path / "last_run.json")
    cache.write(
        LastRun(
            ts="2026-05-01T00:00:00+00:00",
            symptom=None,
            fingerprint=["a"],
        )
    )
    cache.write(
        LastRun(
            ts="2026-05-13T00:00:00+00:00",
            symptom="new",
            fingerprint=["b"],
        )
    )
    loaded = cache.read()
    assert loaded is not None
    assert loaded.symptom == "new"
    assert loaded.fingerprint == ["b"]


def test_read_corrupt_file_returns_none(tmp_path: Path):
    target = tmp_path / "broken.json"
    target.write_text("{not valid json")
    cache = LastRunCache(target)
    assert cache.read() is None
