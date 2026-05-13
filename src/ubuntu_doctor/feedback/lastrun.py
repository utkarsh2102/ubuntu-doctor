"""Tiny JSON cache of the most recent `ubuntu-doctor` run.

Why this exists: `ubuntu-doctor feedback` needs to know which diagnosis the
user is about to give feedback on. Re-running the full collector +
analyzer + LLM pipeline just to surface the same hypothesis ids would
double the inference cost. Instead, every `ubuntu-doctor` run writes its top
hypotheses + symptom + fingerprint to a single JSON file, and
`ubuntu-doctor feedback` reads it.

The cache is a single file, atomically replaced. It deliberately stores
*only* what feedback needs (hypothesis ids + titles + suggested fix
commands + the fingerprint). No raw events, no LLM raw_response.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path.home() / ".cache/ubuntu-doctor/last_run.json"


@dataclass
class LastRunHypothesis:
    id: str
    title: str
    analyzer: str
    fix_commands: list[str] = field(default_factory=list)


@dataclass
class LastRun:
    ts: str
    symptom: str | None
    fingerprint: list[str]
    hypotheses: list[LastRunHypothesis] = field(default_factory=list)


class LastRunCache:
    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path is not None else DEFAULT_PATH

    @property
    def path(self) -> Path:
        return self._path

    def write(self, run: LastRun) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(run)
        # Atomic write so a crashed `ubuntu-doctor` doesn't leave a half-baked
        # cache that breaks the next `ubuntu-doctor feedback`.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".last_run.", dir=self._path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def read(self) -> LastRun | None:
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            return None
        hypotheses = [
            LastRunHypothesis(**h) for h in payload.get("hypotheses", [])
        ]
        return LastRun(
            ts=payload.get("ts", ""),
            symptom=payload.get("symptom"),
            fingerprint=list(payload.get("fingerprint", [])),
            hypotheses=hypotheses,
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
