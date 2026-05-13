"""SQLite-backed local incident memory.

Schema (created on first open if missing):

    incident(id, ts, fingerprint, chosen_hypothesis_ids,
             suggested_fix_commands, applied_commands,
             observed_effect, outcome, notes, revisited_at)

Similarity search is Jaccard over the fingerprint token set — fast,
interpretable, no embeddings required. If recall turns out to be poor
in practice we can swap to `sqlite-vec` without changing this API.

The store is single-user and single-machine; no concurrent-writer
concerns. Uses WAL mode for safety against partial writes on power loss.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ubuntu_doctor.feedback.fingerprint import jaccard

DEFAULT_DB_PATH = Path.home() / ".local/share/ubuntu-doctor/incidents.db"

OUTCOME_VALUES = (
    "fixed",
    "partially-fixed",
    "not-fixed",
    "made-it-worse",
    "unknown",
)


@dataclass(frozen=True)
class Incident:
    fingerprint: list[str]
    chosen_hypothesis_ids: list[str] = field(default_factory=list)
    suggested_fix_commands: list[str] = field(default_factory=list)
    applied_commands: list[str] = field(default_factory=list)
    observed_effect: str = ""
    outcome: str = "unknown"
    notes: str = ""
    id: int | None = None
    ts: datetime | None = None
    revisited_at: datetime | None = None
    similarity: float | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS incident (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    chosen_hypothesis_ids TEXT NOT NULL,
    suggested_fix_commands TEXT NOT NULL,
    applied_commands TEXT NOT NULL,
    observed_effect TEXT NOT NULL,
    outcome TEXT NOT NULL,
    notes TEXT NOT NULL,
    revisited_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_incident_ts ON incident(ts);
CREATE INDEX IF NOT EXISTS idx_incident_outcome ON incident(outcome);
"""


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _row_to_incident(row: sqlite3.Row) -> Incident:
    return Incident(
        id=row["id"],
        ts=_parse_ts(row["ts"]),
        fingerprint=json.loads(row["fingerprint"]),
        chosen_hypothesis_ids=json.loads(row["chosen_hypothesis_ids"]),
        suggested_fix_commands=json.loads(row["suggested_fix_commands"]),
        applied_commands=json.loads(row["applied_commands"]),
        observed_effect=row["observed_effect"],
        outcome=row["outcome"],
        notes=row["notes"],
        revisited_at=_parse_ts(row["revisited_at"]),
    )


class IncidentStore:
    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(self, incident: Incident) -> int:
        if incident.outcome not in OUTCOME_VALUES:
            raise ValueError(
                f"outcome must be one of {OUTCOME_VALUES}; got {incident.outcome!r}"
            )
        ts = (incident.ts or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO incident (
                    ts, fingerprint, chosen_hypothesis_ids,
                    suggested_fix_commands, applied_commands,
                    observed_effect, outcome, notes, revisited_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    json.dumps(incident.fingerprint),
                    json.dumps(incident.chosen_hypothesis_ids),
                    json.dumps(incident.suggested_fix_commands),
                    json.dumps(incident.applied_commands),
                    incident.observed_effect,
                    incident.outcome,
                    incident.notes,
                    incident.revisited_at.isoformat()
                    if incident.revisited_at
                    else None,
                ),
            )
            return cursor.lastrowid

    def all(self) -> list[Incident]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM incident ORDER BY ts DESC"
            ).fetchall()
        return [_row_to_incident(r) for r in rows]

    def find_similar(
        self,
        fingerprint: list[str],
        *,
        top_k: int = 3,
        min_similarity: float = 0.2,
    ) -> list[Incident]:
        """Return the top-k incidents whose fingerprint has Jaccard
        similarity ≥ `min_similarity` with the supplied one. Sorted
        most-similar first; ties broken by recency."""
        if not fingerprint:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM incident").fetchall()
        scored: list[Incident] = []
        for row in rows:
            inc = _row_to_incident(row)
            sim = jaccard(fingerprint, inc.fingerprint)
            if sim < min_similarity:
                continue
            scored.append(
                Incident(
                    id=inc.id,
                    ts=inc.ts,
                    fingerprint=inc.fingerprint,
                    chosen_hypothesis_ids=inc.chosen_hypothesis_ids,
                    suggested_fix_commands=inc.suggested_fix_commands,
                    applied_commands=inc.applied_commands,
                    observed_effect=inc.observed_effect,
                    outcome=inc.outcome,
                    notes=inc.notes,
                    revisited_at=inc.revisited_at,
                    similarity=sim,
                )
            )
        scored.sort(
            key=lambda i: (
                -(i.similarity or 0),
                -(i.ts.timestamp() if i.ts else 0),
            )
        )
        return scored[:top_k]

    def get(self, incident_id: int) -> Incident | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incident WHERE id = ?", (incident_id,)
            ).fetchone()
        return _row_to_incident(row) if row else None
