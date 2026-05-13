"""Types returned by the RAG layer.

A `RetrievedSnippet` is a compact, structured piece of reference text
fetched on-demand from the local filesystem and scoped to one
hypothesis. Snippets are concatenated into the LLM prompt so the model
can reason about specifics (changelog wording, profile rules, crash
metadata) without us having to flatten everything into the snapshot
event stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Soft per-snippet cap. Hard token control still happens in the prompt
# builder; this just prevents one huge file (e.g. a 200 KB changelog)
# from dominating.
MAX_SNIPPET_CHARS = 2048


@dataclass(frozen=True)
class RetrievedSnippet:
    source: str        # Stable id, e.g. "changelog:linux-firmware"
    kind: str          # "changelog" | "news" | "apparmor_profile" | "apport" | "incident_memory"
    title: str         # One-line human label, e.g. "Changelog for linux-firmware (20240318-0ubuntu3.7 → 3.8)"
    content: str       # The snippet body (truncated to MAX_SNIPPET_CHARS)
    related_hypothesis_ids: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict = field(default_factory=dict)


def truncate(text: str, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars - 80]
    return f"{head}\n[... {len(text) - len(head)} more chars truncated by ubuntu-doctor ...]"
