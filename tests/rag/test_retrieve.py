from __future__ import annotations

import gzip
from datetime import datetime, timezone
from pathlib import Path

from ubuntu_doctor.rag.retrieve import retrieve_for_hypotheses
from ubuntu_doctor.snapshot import EventKind, Hypothesis, TimelineEvent

T0 = datetime(2026, 5, 10, 14, 30, tzinfo=timezone.utc)


def _upgrade(pkg: str, old: str = "1.0", new: str = "1.1") -> TimelineEvent:
    return TimelineEvent(
        ts=T0,
        kind=EventKind.PACKAGE_UPGRADE,
        source="dpkg_history",
        subject=pkg,
        summary=f"{pkg} upgraded",
        details={"old_version": old, "new_version": new},
    )


def _denial(profile: str = "snap.spotify.spotify") -> TimelineEvent:
    return TimelineEvent(
        ts=T0,
        kind=EventKind.APPARMOR_DENIED,
        source="journald",
        subject=profile,
        summary=f"{profile} denied open on /some/path",
        details={"profile": profile, "operation": "open"},
    )


def _hyp(id_: str, evidence: tuple[TimelineEvent, ...]) -> Hypothesis:
    return Hypothesis(
        id=id_,
        analyzer="test",
        title=f"hyp {id_}",
        confidence=0.5,
        evidence=evidence,
    )


CHANGELOG = """\
linux-firmware (1.1) noble; urgency=medium

  * Fix regression in audio firmware

 -- Ubuntu  Mon, 01 Apr 2026 10:00:00 +0000

linux-firmware (1.0) noble; urgency=medium

  * Initial release

 -- Ubuntu  Fri, 01 Mar 2026 10:00:00 +0000
"""

APPARMOR = """\
profile snap.spotify.spotify (attach_disconnected) {
  /home/** r,
}
"""


async def test_retrieve_finds_changelog_for_upgrade(tmp_path: Path):
    doc_dir = tmp_path / "docs"
    (doc_dir / "linux-firmware").mkdir(parents=True)
    with gzip.open(
        doc_dir / "linux-firmware" / "changelog.Debian.gz", "wt"
    ) as fh:
        fh.write(CHANGELOG)

    hyp = _hyp("h1", (_upgrade("linux-firmware", "1.0", "1.1"),))
    snippets = await retrieve_for_hypotheses([hyp], base_doc_dir=doc_dir)
    kinds = sorted(s.kind for s in snippets)
    assert "changelog" in kinds
    changelog = next(s for s in snippets if s.kind == "changelog")
    assert "Fix regression in audio firmware" in changelog.content
    assert hyp.id in changelog.related_hypothesis_ids


async def test_retrieve_finds_apparmor_profile(tmp_path: Path):
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "snap.spotify.spotify").write_text(APPARMOR)

    hyp = _hyp("h1", (_denial("snap.spotify.spotify"),))
    snippets = await retrieve_for_hypotheses(
        [hyp], apparmor_dirs=(aa,)
    )
    assert any(s.kind == "apparmor_profile" for s in snippets)
    profile = next(s for s in snippets if s.kind == "apparmor_profile")
    assert "snap.spotify.spotify" in profile.content


async def test_retrieve_deduplicates_across_hypotheses(tmp_path: Path):
    doc_dir = tmp_path / "docs"
    (doc_dir / "linux-firmware").mkdir(parents=True)
    with gzip.open(
        doc_dir / "linux-firmware" / "changelog.Debian.gz", "wt"
    ) as fh:
        fh.write(CHANGELOG)

    # Two hypotheses both reference the same upgrade — we should still
    # only return one changelog snippet, with both hypothesis ids in
    # related_hypothesis_ids.
    upgrade = _upgrade("linux-firmware", "1.0", "1.1")
    h1 = _hyp("h1", (upgrade,))
    h2 = _hyp("h2", (upgrade,))
    snippets = await retrieve_for_hypotheses(
        [h1, h2], base_doc_dir=doc_dir
    )
    changelogs = [s for s in snippets if s.kind == "changelog"]
    assert len(changelogs) == 1
    related = set(changelogs[0].related_hypothesis_ids)
    assert {"h1", "h2"}.issubset(related)


async def test_retrieve_with_no_hypotheses_is_empty(tmp_path: Path):
    assert await retrieve_for_hypotheses([], base_doc_dir=tmp_path) == []


async def test_retrieve_skips_when_no_relevant_evidence(tmp_path: Path):
    # A hypothesis with only service-failed evidence and no matching
    # apport reports should retrieve nothing (and not crash).
    failure = TimelineEvent(
        ts=T0,
        kind=EventKind.SERVICE_FAILED,
        source="systemd_failed",
        subject="nonexistent.service",
        summary="nonexistent failed",
    )
    snippets = await retrieve_for_hypotheses(
        [_hyp("h", (failure,))],
        base_doc_dir=tmp_path,
        crash_dir=tmp_path / "crash",
    )
    assert snippets == []
