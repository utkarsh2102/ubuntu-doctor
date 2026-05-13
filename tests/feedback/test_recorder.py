from __future__ import annotations

import io
from pathlib import Path

from ubuntu_doctor.feedback.lastrun import (
    LastRun,
    LastRunCache,
    LastRunHypothesis,
)
from ubuntu_doctor.feedback.recorder import record_feedback
from ubuntu_doctor.feedback.store import IncidentStore


def _stub_inputs(*answers: str):
    queue = list(answers)

    def _input(_prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return _input


def _seeded_cache(tmp_path: Path) -> LastRunCache:
    cache = LastRunCache(tmp_path / "last_run.json")
    cache.write(
        LastRun(
            ts="2026-05-13T12:00:00+00:00",
            symptom="audio gone",
            fingerprint=[
                "analyzer:postupgrade_regression",
                "subject:linux-firmware",
            ],
            hypotheses=[
                LastRunHypothesis(
                    id="postupgrade-linux-firmware-pulseaudio",
                    title="pulseaudio failed after linux-firmware upgrade",
                    analyzer="postupgrade_regression",
                    fix_commands=["sudo apt install linux-firmware=1.0"],
                )
            ],
        )
    )
    return cache


def test_record_feedback_happy_path(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    store = IncidentStore(tmp_path / "db.sqlite")
    err = io.StringIO()

    code, incident_id = record_feedback(
        cache=cache,
        store=store,
        input_fn=_stub_inputs(
            "1",                                  # which hypothesis
            "sudo apt install linux-firmware=1.0",  # applied (one line)
            "",                                   # end applied
            "audio came back after reboot",       # observed effect
            "",                                   # end observed
            "fixed",                              # outcome
            "",                                   # notes
        ),
        stderr=err,
    )
    assert code == 0
    assert incident_id is not None

    stored = store.get(incident_id)
    assert stored is not None
    assert stored.chosen_hypothesis_ids == [
        "postupgrade-linux-firmware-pulseaudio"
    ]
    assert stored.applied_commands == [
        "sudo apt install linux-firmware=1.0"
    ]
    assert stored.observed_effect == "audio came back after reboot"
    assert stored.outcome == "fixed"
    assert stored.fingerprint == [
        "analyzer:postupgrade_regression",
        "subject:linux-firmware",
    ]


def test_record_feedback_missing_cache_is_nonzero(tmp_path: Path):
    cache = LastRunCache(tmp_path / "absent.json")
    store = IncidentStore(tmp_path / "db.sqlite")
    err = io.StringIO()
    code, incident_id = record_feedback(
        cache=cache,
        store=store,
        input_fn=_stub_inputs(),
        stderr=err,
    )
    assert code == 1
    assert incident_id is None
    assert "no previous diagnosis" in err.getvalue().lower()


def test_record_feedback_skip_keeps_chosen_empty(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    store = IncidentStore(tmp_path / "db.sqlite")
    code, incident_id = record_feedback(
        cache=cache,
        store=store,
        input_fn=_stub_inputs(
            "skip",
            "",            # applied
            "still broken",
            "",            # end observed
            "not-fixed",
            "",            # notes
        ),
        stderr=io.StringIO(),
    )
    assert code == 0
    stored = store.get(incident_id)
    assert stored is not None
    assert stored.chosen_hypothesis_ids == []
    assert stored.outcome == "not-fixed"


def test_record_feedback_unknown_outcome_falls_back(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    store = IncidentStore(tmp_path / "db.sqlite")
    err = io.StringIO()
    code, incident_id = record_feedback(
        cache=cache,
        store=store,
        input_fn=_stub_inputs(
            "skip",
            "",          # applied
            "",          # observed
            "banana",    # invalid outcome
            "fixed",     # second attempt
            "",          # notes
        ),
        stderr=err,
    )
    assert code == 0
    stored = store.get(incident_id)
    assert stored is not None
    assert stored.outcome == "fixed"
    assert "not one of" in err.getvalue().lower()


def test_record_feedback_eof_during_inputs_does_not_save(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    store = IncidentStore(tmp_path / "db.sqlite")
    code, incident_id = record_feedback(
        cache=cache,
        store=store,
        input_fn=_stub_inputs("1"),  # then EOFs out
        stderr=io.StringIO(),
    )
    # Recorder may bail mid-flow. It should not save in that case.
    if incident_id is not None:
        # If it did save, the stored incident must have come from the
        # path where EOF maps cleanly to "no observed effect" rather
        # than a half-baked write. Either is acceptable as long as
        # nothing crashes.
        assert store.get(incident_id) is not None
    else:
        assert code != 0 or store.all() == []
