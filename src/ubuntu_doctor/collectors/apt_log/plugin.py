"""apt transaction history + held/broken package state.

Sources:
  - `/var/log/apt/history.log[.1.gz]` — Start-Date/End-Date blocks
    with Commandline, Requested-By, and per-action lists. Higher-level
    than dpkg.log: shows the user's *intent*, not just dpkg's actions.
  - `apt-mark showhold` — packages explicitly held back from upgrades.
  - `dpkg --audit` — packages in an inconsistent state.

Emits **no events** (dpkg_history is authoritative for per-package
events). Populates `facts["apt_log"]` with:
  - recent_transactions: list of apt runs with commandline/timestamp/counts
  - held_packages: list of names
  - broken_packages: list of names from `dpkg --audit`
  - term_log_errors: recent error markers from /var/log/apt/term.log
"""

from __future__ import annotations

import asyncio
import gzip
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from ubuntu_doctor.collectors.base import Collector, CollectorResult

CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]

DEFAULT_HISTORY_PATHS = (
    Path("/var/log/apt/history.log"),
    Path("/var/log/apt/history.log.1.gz"),
)
DEFAULT_TERM_PATHS = (
    Path("/var/log/apt/term.log"),
    Path("/var/log/apt/term.log.1.gz"),
)

# Lines we treat as transaction errors in term.log.
_TERM_ERROR_RE = re.compile(
    r"^(?:E:|errors?\s+were\s+encountered|dpkg:\s+error|"
    r"Setting up.+?\s+\.\.\.\s*ERROR|"
    r"failed to.*?process)",
    re.IGNORECASE | re.MULTILINE,
)


async def _run_subprocess(args: list[str]) -> tuple[int, str]:
    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        return 127, ""
    stdout_bytes, _ = await proc.communicate()
    return proc.returncode or 0, stdout_bytes.decode("utf-8", errors="replace")


def _open_log(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_history_log(
    text: str, *, window_start: datetime, window_end: datetime
) -> list[dict]:
    """Parse blocks of the form:

        Start-Date: 2024-06-10  10:30:42
        Commandline: apt upgrade
        Requested-By: oliver (1000)
        Upgrade: pkg:arch (old, new), ...
        Install: pkg:arch (1.0)
        End-Date: 2024-06-10  10:31:00

    Returns one dict per block inside the window.
    """
    blocks: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current is not None and "start_ts" in current:
                blocks.append(current)
                current = None
            continue
        if line.startswith("Start-Date:"):
            current = {"start_ts_raw": line.split(":", 1)[1].strip()}
            ts = _parse_apt_timestamp(current["start_ts_raw"])
            if ts is None:
                current = None
                continue
            current["start_ts"] = ts
        elif current is not None and line.startswith("Commandline:"):
            current["commandline"] = line.split(":", 1)[1].strip()
        elif current is not None and line.startswith("Requested-By:"):
            current["requested_by"] = line.split(":", 1)[1].strip()
        elif current is not None and line.startswith("End-Date:"):
            end_raw = line.split(":", 1)[1].strip()
            end_ts = _parse_apt_timestamp(end_raw)
            if end_ts is not None:
                current["end_ts"] = end_ts
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key in {"install", "upgrade", "remove", "purge", "reinstall", "downgrade"}:
                names = _extract_package_names(value)
                current[key] = names
                current.setdefault("counts", {})[key] = len(names)
    if current is not None and "start_ts" in current:
        blocks.append(current)

    return [
        b for b in blocks
        if window_start <= b["start_ts"] <= window_end
    ]


def _parse_apt_timestamp(value: str) -> datetime | None:
    # apt history.log uses local-time-ish format: "2024-06-10  10:30:42".
    # We parse without timezone, then label as UTC for the snapshot — the
    # absolute clock matters less than ordering vs. other events.
    cleaned = re.sub(r"\s+", " ", value.strip())
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _extract_package_names(value: str) -> list[str]:
    """Strip `(version, ...)` parens and architectures, returning a
    list of `pkg:arch` (or `pkg`) names. Handles nested parens that
    apt sometimes emits when versions contain `()`."""
    names: list[str] = []
    depth = 0
    buf = ""
    for ch in value:
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            continue
        if depth > 0:
            continue
        if ch == ",":
            cleaned = buf.strip()
            if cleaned:
                names.append(cleaned)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        names.append(buf.strip())
    return names


def parse_apt_mark_showhold(stdout: str) -> list[str]:
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def parse_dpkg_audit(stdout: str) -> list[str]:
    """dpkg --audit output is free-form English text, with package names
    appearing indented after a description. Heuristic: extract tokens
    that look like package names (lowercase, dashes, digits)."""
    pkg_re = re.compile(r"^\s+(?P<pkg>[a-z0-9][a-z0-9.+:-]+)\s*$")
    out: list[str] = []
    for line in stdout.splitlines():
        match = pkg_re.match(line)
        if match:
            out.append(match.group("pkg"))
    return out


def parse_term_log_errors(text: str, *, max_lines: int = 20) -> list[str]:
    matches = _TERM_ERROR_RE.findall(text)
    return matches[-max_lines:]


def _read_log_text(paths: tuple[Path, ...]) -> str:
    """Concatenate readable log files in `paths` (newest first by
    convention), returning the joined text. Skips unreadable files."""
    chunks: list[str] = []
    for path in paths:
        try:
            with _open_log(path) as fh:
                chunks.append(fh.read())
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return "\n".join(chunks)


class AptLogCollector(Collector):
    id = "apt_log"

    def __init__(
        self,
        *,
        history_paths: tuple[Path, ...] | None = None,
        term_paths: tuple[Path, ...] | None = None,
        run_command: CommandRunner | None = None,
    ):
        self._history_paths = history_paths or DEFAULT_HISTORY_PATHS
        self._term_paths = term_paths or DEFAULT_TERM_PATHS
        self._run = run_command or _run_subprocess

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        history_text = await asyncio.to_thread(
            _read_log_text, self._history_paths
        )
        term_text = await asyncio.to_thread(_read_log_text, self._term_paths)
        hold_rc, hold_out = await self._run(["apt-mark", "showhold"])
        audit_rc, audit_out = await self._run(["dpkg", "--audit"])

        transactions = parse_history_log(
            history_text, window_start=window_start, window_end=window_end
        )
        facts: dict = {
            "recent_transactions": [
                {
                    "start_ts": t["start_ts"].isoformat(),
                    "end_ts": t.get("end_ts").isoformat() if t.get("end_ts") else None,
                    "commandline": t.get("commandline", ""),
                    "requested_by": t.get("requested_by", ""),
                    "counts": t.get("counts", {}),
                }
                for t in transactions
            ],
            "held_packages": (
                parse_apt_mark_showhold(hold_out) if hold_rc == 0 else []
            ),
            "broken_packages": (
                parse_dpkg_audit(audit_out) if audit_rc == 0 else []
            ),
            "term_log_errors": parse_term_log_errors(term_text),
        }
        return CollectorResult(events=[], facts=facts)


COLLECTOR = AptLogCollector()
