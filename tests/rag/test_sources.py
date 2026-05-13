from __future__ import annotations

import gzip
from pathlib import Path

from ubuntu_doctor.rag.sources import (
    extract_profile_block,
    fetch_apparmor_profile,
    fetch_apport_reports,
    fetch_changelog,
    fetch_news,
    parse_apport_report,
    read_changelog_between,
)


CHANGELOG_TEXT = """\
linux-firmware (20240318-0ubuntu3.8) noble; urgency=medium

  * Pull from upstream linux-firmware:
    - rtl_nic: regression on rtl8125a-3 fixed (LP: #2050000)

 -- Ubuntu Kernel <ubuntu-kernel@lists.ubuntu.com>  Mon, 01 Apr 2026 10:00:00 +0000

linux-firmware (20240318-0ubuntu3.7) noble; urgency=medium

  * Pull from upstream linux-firmware

 -- Ubuntu Kernel <ubuntu-kernel@lists.ubuntu.com>  Tue, 11 Mar 2026 10:00:00 +0000

linux-firmware (20240318-0ubuntu3.6) noble; urgency=medium

  * Initial upload for 24.04

 -- Ubuntu Kernel <ubuntu-kernel@lists.ubuntu.com>  Fri, 01 Mar 2026 10:00:00 +0000
"""


def test_read_changelog_between_returns_entries_in_range():
    text = read_changelog_between(
        CHANGELOG_TEXT,
        old_version="20240318-0ubuntu3.7",
        new_version="20240318-0ubuntu3.8",
    )
    assert "20240318-0ubuntu3.8" in text
    # We want the *new* entry (3.8), not the old one (3.7) the user
    # already had installed, and definitely not the older 3.6.
    assert "20240318-0ubuntu3.7" not in text
    assert "20240318-0ubuntu3.6" not in text
    assert "rtl8125a-3 fixed" in text


def test_read_changelog_between_with_no_matches_returns_empty():
    text = read_changelog_between(
        CHANGELOG_TEXT,
        old_version="20240318-0ubuntu3.8",
        new_version="20240318-0ubuntu3.8",
    )
    assert text == ""


def test_read_changelog_handles_malformed_input():
    assert read_changelog_between("not a changelog at all", "1.0", "1.1") == ""


def test_fetch_changelog_reads_gz(tmp_path: Path):
    doc_dir = tmp_path / "linux-firmware"
    doc_dir.mkdir()
    target = doc_dir / "changelog.Debian.gz"
    with gzip.open(target, "wt", encoding="utf-8") as fh:
        fh.write(CHANGELOG_TEXT)

    out = fetch_changelog(
        "linux-firmware",
        "20240318-0ubuntu3.7",
        "20240318-0ubuntu3.8",
        base_doc_dir=tmp_path,
    )
    assert out is not None
    assert "rtl8125a-3" in out


def test_fetch_changelog_returns_none_for_missing_package(tmp_path: Path):
    assert (
        fetch_changelog(
            "nonexistent-pkg", "1.0", "1.1", base_doc_dir=tmp_path
        )
        is None
    )


def test_fetch_news_returns_truncated_content(tmp_path: Path):
    doc_dir = tmp_path / "snapd"
    doc_dir.mkdir()
    target = doc_dir / "NEWS.Debian.gz"
    news_body = "snapd news entry\n" + ("x" * 5000)
    with gzip.open(target, "wt", encoding="utf-8") as fh:
        fh.write(news_body)
    out = fetch_news("snapd", base_doc_dir=tmp_path)
    assert out is not None
    assert "snapd news entry" in out
    # Must be capped well under the original size (5KB) — truncation in effect.
    assert len(out) <= 2200


# ---------------------------------------------------------------------------
# AppArmor profile lookup
# ---------------------------------------------------------------------------


APPARMOR_PROFILE_TEXT = """\
#include <tunables/global>

profile snap.spotify.spotify (attach_disconnected) {
  #include <abstractions/base>
  /home/** r,
  /etc/passwd r,
}

profile snap.firefox.firefox (attach_disconnected) {
  /home/** r,
}
"""


def test_extract_profile_block_balances_braces():
    block = extract_profile_block(
        APPARMOR_PROFILE_TEXT, "snap.spotify.spotify"
    )
    assert block is not None
    assert block.startswith("profile snap.spotify.spotify")
    assert block.endswith("}")
    assert "/home/** r," in block
    # Must not bleed into the firefox profile.
    assert "snap.firefox.firefox" not in block


def test_extract_profile_block_missing_returns_none():
    assert extract_profile_block(APPARMOR_PROFILE_TEXT, "snap.missing") is None


def test_fetch_apparmor_profile_walks_dir(tmp_path: Path):
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    profile_path = aa / "snap.spotify.spotify"
    profile_path.write_text(APPARMOR_PROFILE_TEXT)
    out = fetch_apparmor_profile(
        "snap.spotify.spotify", profile_dirs=(aa,)
    )
    assert out is not None
    assert "snap.spotify.spotify" in out


def test_fetch_apparmor_profile_missing(tmp_path: Path):
    assert (
        fetch_apparmor_profile("snap.absent", profile_dirs=(tmp_path,))
        is None
    )


# ---------------------------------------------------------------------------
# Apport reports
# ---------------------------------------------------------------------------


APPORT_TEXT = """\
ProblemType: Crash
Architecture: amd64
DistroRelease: Ubuntu 24.04
ExecutablePath: /usr/bin/spotify
Package: spotify-client 1.2.3.456-1
Signal: 11
Uname: Linux 6.8.0-50-generic
ProcMaps:
 7f0000000000-7f0000020000 r--p 00000000 00:00 0
 7f0000020000-7f0000030000 rwxp 00000000 00:00 0
Stacktrace:
 #0 0x00007f0... in spotify_main ()
"""


def test_parse_apport_report_picks_known_keys():
    parsed = parse_apport_report(APPORT_TEXT)
    assert parsed["ExecutablePath"] == "/usr/bin/spotify"
    assert parsed["Signal"] == "11"
    assert parsed["Architecture"] == "amd64"
    # Bulky multi-line stuff is excluded.
    assert "ProcMaps" not in parsed
    assert "Stacktrace" not in parsed


def test_fetch_apport_reports_matches_basename(tmp_path: Path):
    (tmp_path / "_usr_bin_spotify.42.crash").write_text(APPORT_TEXT)
    (tmp_path / "_usr_bin_other.42.crash").write_text(
        APPORT_TEXT.replace("/usr/bin/spotify", "/usr/bin/firefox")
    )
    out = fetch_apport_reports({"spotify"}, crash_dir=tmp_path)
    assert len(out) == 1
    filename, parsed = out[0]
    assert filename.endswith(".crash")
    assert parsed["ExecutablePath"] == "/usr/bin/spotify"


def test_fetch_apport_reports_with_no_matches(tmp_path: Path):
    (tmp_path / "report.crash").write_text(APPORT_TEXT)
    assert fetch_apport_reports({"nope"}, crash_dir=tmp_path) == []


def test_fetch_apport_reports_handles_missing_dir(tmp_path: Path):
    assert (
        fetch_apport_reports({"x"}, crash_dir=tmp_path / "does-not-exist")
        == []
    )
