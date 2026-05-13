"""Maps top hypotheses to a small set of `RetrievedSnippet`s.

Wiring rules (deliberately simple, deliberately scoped):

  - For each `PACKAGE_UPGRADE` event in evidence → fetch the changelog
    entries between old and new versions, plus NEWS for that package
    if a NEWS.Debian.gz exists.
  - For each `APPARMOR_DENIED` event in evidence → fetch the profile
    block from /etc/apparmor.d or /var/lib/snapd/apparmor/profiles.
  - For each `SERVICE_FAILED` event in evidence whose unit name maps
    to a known executable → look for matching apport crash reports.
  - De-duplicate by `(kind, source)` so we don't re-fetch the same
    changelog for two related hypotheses.

Retrieval runs concurrently via asyncio.to_thread because each
sub-fetch is filesystem-bound, not CPU-bound.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ubuntu_doctor.rag.sources import (
    fetch_apparmor_profile,
    fetch_apport_reports,
    fetch_changelog,
    fetch_news,
)
from ubuntu_doctor.rag.types import RetrievedSnippet
from ubuntu_doctor.snapshot import EventKind, Hypothesis

MAX_HYPOTHESES_FOR_RETRIEVAL = 5
MAX_SNIPPETS_PER_HYPOTHESIS = 4


def _executable_basename_for_unit(unit: str) -> str:
    """Cheap heuristic: `rsyslog.service` → `rsyslogd`. Many units are
    `<binary>.service` or `<binary>d.service`. We try both."""
    base = unit.rsplit(".", 1)[0]
    return base


def _executable_candidates(unit: str) -> set[str]:
    base = _executable_basename_for_unit(unit)
    return {base, f"{base}d"}


async def _retrieve_for_one(
    hypothesis: Hypothesis,
    *,
    base_doc_dir: Path | None,
    apparmor_dirs: tuple[Path, ...] | None,
    crash_dir: Path | None,
) -> list[RetrievedSnippet]:
    # Plan the fetches first (so we keep a parallel ordered list of
    # (kind, source) keys), then dispatch them all concurrently. Set
    # iteration order is not guaranteed, so we use an explicit list.
    plan: list[tuple[str, str]] = []
    fetches: list = []
    package_pairs: list[tuple[str, str, str]] = []  # (pkg, old, new)

    def _queue_changelog(pkg: str, old: str, new: str) -> None:
        key = ("changelog", pkg)
        if key in plan:
            return
        plan.append(key)
        fetches.append(
            asyncio.to_thread(
                fetch_changelog, pkg, old, new, base_doc_dir=base_doc_dir
            )
        )

    def _queue_news(pkg: str) -> None:
        key = ("news", pkg)
        if key in plan:
            return
        plan.append(key)
        fetches.append(
            asyncio.to_thread(fetch_news, pkg, base_doc_dir=base_doc_dir)
        )

    def _queue_apparmor(profile: str) -> None:
        key = ("apparmor_profile", profile)
        if key in plan:
            return
        plan.append(key)
        kwargs = {}
        if apparmor_dirs is not None:
            kwargs["profile_dirs"] = apparmor_dirs
        fetches.append(
            asyncio.to_thread(fetch_apparmor_profile, profile, **kwargs)
        )

    apport_basenames: set[str] = set()
    apparmor_profiles: list[str] = []

    for event in hypothesis.evidence:
        if event.kind in (
            EventKind.PACKAGE_UPGRADE,
            EventKind.PACKAGE_INSTALL,
        ):
            old = event.details.get("old_version", "") or ""
            new = event.details.get("new_version", "") or ""
            if not new or old == "<none>":
                _queue_news(event.subject)
                continue
            package_pairs.append((event.subject, old, new))
        elif event.kind == EventKind.APPARMOR_DENIED:
            apparmor_profiles.append(event.subject)
        elif event.kind == EventKind.SERVICE_FAILED:
            apport_basenames.update(_executable_candidates(event.subject))

    for pkg, old, new in package_pairs:
        _queue_changelog(pkg, old, new)
        _queue_news(pkg)
    for profile in apparmor_profiles:
        _queue_apparmor(profile)

    apport_coro = None
    if apport_basenames:
        kwargs = {}
        if crash_dir is not None:
            kwargs["crash_dir"] = crash_dir
        apport_coro = asyncio.to_thread(
            fetch_apport_reports, apport_basenames, **kwargs
        )

    results = await asyncio.gather(*fetches, return_exceptions=True)

    snippets: list[RetrievedSnippet] = []
    for (kind, source), result in zip(plan, results, strict=True):
        if isinstance(result, Exception):
            continue
        if result is None or (isinstance(result, str) and not result.strip()):
            continue
        if kind == "changelog":
            old, new = next(
                ((o, n) for p, o, n in package_pairs if p == source),
                ("", ""),
            )
            title = (
                f"Changelog for {source} ({old} → {new})"
                if old and new
                else f"Changelog for {source}"
            )
        elif kind == "news":
            title = f"NEWS for {source}"
        elif kind == "apparmor_profile":
            title = f"AppArmor profile {source}"
        else:
            title = source
        snippets.append(
            RetrievedSnippet(
                source=f"{kind}:{source}",
                kind=kind,
                title=title,
                content=result,
                related_hypothesis_ids=(hypothesis.id,),
            )
        )

    if apport_coro is not None:
        try:
            apport_results = await apport_coro
        except Exception:
            apport_results = []
        if not isinstance(apport_results, Exception):
            for filename, parsed in apport_results:
                lines = [f"{k}: {v}" for k, v in parsed.items()]
                snippets.append(
                    RetrievedSnippet(
                        source=f"apport:{filename}",
                        kind="apport",
                        title=f"Apport report {filename}",
                        content="\n".join(lines),
                        related_hypothesis_ids=(hypothesis.id,),
                        metadata=parsed,
                    )
                )

    return snippets[:MAX_SNIPPETS_PER_HYPOTHESIS]


async def retrieve_for_hypotheses(
    hypotheses: list[Hypothesis],
    *,
    base_doc_dir: Path | None = None,
    apparmor_dirs: tuple[Path, ...] | None = None,
    crash_dir: Path | None = None,
) -> list[RetrievedSnippet]:
    """Concurrently fetch reference material for the top N hypotheses.

    The orchestrator only feeds the top hypotheses through retrieval —
    snippets are not cheap (they cost tokens) and the LLM will mostly
    care about whatever is ranked first anyway.
    """
    if not hypotheses:
        return []
    top = hypotheses[:MAX_HYPOTHESES_FOR_RETRIEVAL]
    per_hyp = await asyncio.gather(
        *(
            _retrieve_for_one(
                h,
                base_doc_dir=base_doc_dir,
                apparmor_dirs=apparmor_dirs,
                crash_dir=crash_dir,
            )
            for h in top
        ),
        return_exceptions=False,
    )
    # Deduplicate across hypotheses by (kind, source).
    seen: dict[tuple[str, str], RetrievedSnippet] = {}
    for batch in per_hyp:
        for snippet in batch:
            key = (snippet.kind, snippet.source)
            if key in seen:
                # Merge related_hypothesis_ids so downstream knows
                # which hypotheses share this snippet.
                existing = seen[key]
                merged = tuple(
                    dict.fromkeys(
                        (*existing.related_hypothesis_ids,
                         *snippet.related_hypothesis_ids)
                    )
                )
                seen[key] = RetrievedSnippet(
                    source=existing.source,
                    kind=existing.kind,
                    title=existing.title,
                    content=existing.content,
                    related_hypothesis_ids=merged,
                    metadata=existing.metadata,
                )
                continue
            seen[key] = snippet
    return list(seen.values())
