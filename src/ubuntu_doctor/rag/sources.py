"""Per-source RAG retrievers.

Each `read_*` function is a pure transformation over text — tests pass
strings directly. The thin `fetch_*` wrappers handle filesystem I/O
(opening the gzipped changelog, walking `/etc/apparmor.d/`, etc.) and
swallow expected errors (FileNotFoundError, PermissionError, gzip
errors) by returning `None` so the caller can move on.

These functions deliberately know nothing about hypotheses or LLM
prompts — the orchestrator in [retrieve.py](retrieve.py) wires them
together.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

from ubuntu_doctor.rag.types import MAX_SNIPPET_CHARS, truncate

# ---------------------------------------------------------------------------
# Debian changelog / NEWS
# ---------------------------------------------------------------------------

# Debian changelog version header:
#   "pkg-name (1.2.3-1ubuntu0.1) focal; urgency=medium"
_CHANGELOG_HEADER_RE = re.compile(
    r"^(?P<pkg>\S+)\s+\((?P<version>[^)]+)\)\s+\S+;",
    re.MULTILINE,
)


def _changelog_paths(package: str) -> tuple[Path, ...]:
    base = Path("/usr/share/doc") / package
    return (
        base / "changelog.Debian.gz",
        base / "changelog.gz",
    )


def _news_paths(package: str) -> tuple[Path, ...]:
    base = Path("/usr/share/doc") / package
    return (
        base / "NEWS.Debian.gz",
        base / "NEWS.gz",
    )


def _read_gzip_text(path: Path) -> str | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (FileNotFoundError, PermissionError, OSError, gzip.BadGzipFile):
        return None


def _version_lt(a: str, b: str) -> bool:
    """Coarse version compare — splits on non-alphanumerics, compares
    component-wise with numeric segments preferred. NOT a substitute
    for `dpkg --compare-versions`, but good enough to decide "include
    this changelog entry or not" between two version strings we already
    know are different.
    """
    if a == b:
        return False
    tok_re = re.compile(r"(\d+|[A-Za-z]+)")
    at = tok_re.findall(a)
    bt = tok_re.findall(b)
    for x, y in zip(at, bt):
        if x.isdigit() and y.isdigit():
            ix, iy = int(x), int(y)
            if ix != iy:
                return ix < iy
        elif x.isdigit() != y.isdigit():
            # Numeric component beats alpha.
            return not x.isdigit()
        else:
            if x != y:
                return x < y
    return len(at) < len(bt)


def read_changelog_between(text: str, old_version: str, new_version: str) -> str:
    """Extract entries whose header version is strictly greater than
    `old_version` and ≤ `new_version`.

    The Debian changelog convention is newest-first. We walk version
    headers and slice the entries that match the predicate. Robust
    against malformed entries — anything that doesn't parse is skipped.
    """
    headers: list[tuple[int, str, str]] = []
    for m in _CHANGELOG_HEADER_RE.finditer(text):
        headers.append((m.start(), m.group("pkg"), m.group("version")))
    if not headers:
        return ""
    # Append a sentinel for end-of-file slicing.
    headers.append((len(text), "", ""))

    kept: list[str] = []
    for idx in range(len(headers) - 1):
        start, _pkg, version = headers[idx]
        end, _, _ = headers[idx + 1]
        if not _version_lt(old_version, version):
            continue
        if _version_lt(new_version, version):
            continue
        entry = text[start:end].rstrip()
        kept.append(entry)
    return "\n\n".join(kept)


def fetch_changelog(
    package: str,
    old_version: str,
    new_version: str,
    *,
    base_doc_dir: Path | None = None,
) -> str | None:
    """Return changelog entries between two versions, or None if no
    changelog is readable for `package`."""
    paths: tuple[Path, ...]
    if base_doc_dir is not None:
        paths = (
            base_doc_dir / package / "changelog.Debian.gz",
            base_doc_dir / package / "changelog.gz",
        )
    else:
        paths = _changelog_paths(package)
    for path in paths:
        raw = _read_gzip_text(path)
        if raw is None:
            continue
        slice_ = read_changelog_between(raw, old_version, new_version)
        if slice_.strip():
            return truncate(slice_, MAX_SNIPPET_CHARS)
        # Found a changelog but no entries in range — return a short
        # marker so the LLM knows we looked.
        return ""
    return None


def fetch_news(
    package: str, *, base_doc_dir: Path | None = None
) -> str | None:
    """NEWS files are narrative, not versioned the same way as
    changelogs — return the whole file (truncated) and let the LLM
    decide what's relevant."""
    paths: tuple[Path, ...]
    if base_doc_dir is not None:
        paths = (
            base_doc_dir / package / "NEWS.Debian.gz",
            base_doc_dir / package / "NEWS.gz",
        )
    else:
        paths = _news_paths(package)
    for path in paths:
        raw = _read_gzip_text(path)
        if raw is not None:
            return truncate(raw, MAX_SNIPPET_CHARS)
    return None


# ---------------------------------------------------------------------------
# AppArmor profile lookup
# ---------------------------------------------------------------------------

_APPARMOR_PROFILE_HEADER_RE = re.compile(
    r"^\s*profile\s+(\S+)\s*(?:\{|.*\{)",
    re.MULTILINE,
)


def extract_profile_block(text: str, profile_name: str) -> str | None:
    """Find `profile <name> { ... }` in an AppArmor profile file and
    return that block, balancing braces. The returned block is
    left-stripped so it starts at the `profile` keyword."""
    match = re.search(
        rf"^\s*(profile\s+{re.escape(profile_name)}\b[^{{]*\{{)",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    start = match.start(1)
    depth = 0
    i = match.end(1) - 1  # position of opening `{`
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def fetch_apparmor_profile(
    profile_name: str,
    *,
    profile_dirs: tuple[Path, ...] = (
        Path("/etc/apparmor.d"),
        Path("/var/lib/snapd/apparmor/profiles"),
    ),
) -> str | None:
    """Look up the on-disk profile by name. Walks the canonical
    AppArmor directories and snap's auto-generated profile dir."""
    for directory in profile_dirs:
        try:
            entries = list(directory.iterdir())
        except (FileNotFoundError, PermissionError):
            continue
        # Cheap heuristic: prefer files whose name contains the
        # profile's last dotted token (e.g. profile `snap.spotify.spotify`
        # → file `snap.spotify.spotify`).
        candidates = sorted(
            (e for e in entries if e.is_file()),
            key=lambda e: 0 if profile_name in e.name else 1,
        )
        for path in candidates:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue
            block = extract_profile_block(content, profile_name)
            if block is not None:
                return truncate(block, MAX_SNIPPET_CHARS)
            # Some snap profiles are a single profile per file with no
            # explicit `profile <name>` header — return the whole file
            # if its name matches the profile.
            if profile_name in path.name:
                return truncate(content, MAX_SNIPPET_CHARS)
    return None


# ---------------------------------------------------------------------------
# Apport crash reports
# ---------------------------------------------------------------------------

# Apport report format is `Key: value` with line continuations indented.
_APPORT_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*):\s*(.*)$")

# Top-level fields worth showing to the LLM. Skipping bulky ones like
# `ProcMaps`, `CoreDump`, `Stacktrace` (they're huge and rarely
# diagnostic in plain-text form).
_APPORT_INTERESTING_KEYS = (
    "ExecutablePath",
    "Signal",
    "ProcCmdline",
    "ProblemType",
    "Package",
    "DistroRelease",
    "Uname",
    "Architecture",
    "AssertionMessage",
    "PythonExc",
    "Traceback",
    "ExecutableTimestamp",
)


def parse_apport_report(text: str) -> dict[str, str]:
    """Return a dict of `Key` → first-line value for keys we care about.
    Multi-line values (apport indents continuation lines) are truncated
    to their first line for compactness; tests assert this."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = _APPORT_KEY_RE.match(line)
        if match is None:
            continue
        key, value = match.group(1), match.group(2)
        if key in _APPORT_INTERESTING_KEYS and key not in out:
            out[key] = value.strip()
    return out


def fetch_apport_reports(
    executable_basenames: set[str],
    *,
    crash_dir: Path = Path("/var/crash"),
) -> list[tuple[str, dict[str, str]]]:
    """Walk `/var/crash`, find reports whose `ExecutablePath` ends with
    one of the requested basenames, and return parsed metadata."""
    try:
        candidates = [p for p in crash_dir.iterdir() if p.suffix == ".crash"]
    except (FileNotFoundError, PermissionError):
        return []
    results: list[tuple[str, dict[str, str]]] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            continue
        parsed = parse_apport_report(text)
        exe = parsed.get("ExecutablePath", "")
        if not exe:
            continue
        basename = exe.rsplit("/", 1)[-1]
        if basename in executable_basenames:
            results.append((path.name, parsed))
    return results
