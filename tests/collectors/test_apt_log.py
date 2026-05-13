from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ubuntu_doctor.collectors.apt_log.plugin import (
    AptLogCollector,
    parse_apt_mark_showhold,
    parse_dpkg_audit,
    parse_history_log,
    parse_term_log_errors,
)

T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 5, 31, tzinfo=timezone.utc)


HISTORY_TEXT = """\
Start-Date: 2026-05-10  10:30:42
Commandline: apt upgrade
Requested-By: oliver (1000)
Upgrade: libfoo:amd64 (1.2-1, 1.3-1), bar:amd64 (2.0, 2.1)
End-Date: 2026-05-10  10:31:00

Start-Date: 2026-05-12  04:00:00
Commandline: /usr/bin/unattended-upgrade
Install: htop:amd64 (3.3.0-3)
End-Date: 2026-05-12  04:00:05

Start-Date: 2026-04-01  00:00:00
Commandline: apt upgrade
Upgrade: foo:amd64 (1, 2)
End-Date: 2026-04-01  00:00:05
"""


def test_parse_history_log_extracts_blocks_in_window():
    out = parse_history_log(HISTORY_TEXT, window_start=T0, window_end=T1)
    assert len(out) == 2  # April block is out of window
    cmdlines = [b["commandline"] for b in out]
    assert "apt upgrade" in cmdlines
    assert "/usr/bin/unattended-upgrade" in cmdlines


def test_parse_history_log_counts_actions():
    out = parse_history_log(HISTORY_TEXT, window_start=T0, window_end=T1)
    upgrade_block = next(
        b for b in out if b["commandline"] == "apt upgrade"
    )
    assert upgrade_block["counts"]["upgrade"] == 2
    install_block = next(
        b for b in out if "unattended" in b["commandline"]
    )
    assert install_block["counts"]["install"] == 1


def test_parse_history_log_handles_versions_with_parens():
    text = (
        "Start-Date: 2026-05-10  10:30:42\n"
        "Commandline: apt upgrade\n"
        "Upgrade: foo:amd64 (1.0 (build1), 1.1 (build2))\n"
        "End-Date: 2026-05-10  10:31:00\n"
    )
    blocks = parse_history_log(text, window_start=T0, window_end=T1)
    assert len(blocks) == 1
    # Nested parens shouldn't confuse the parser into emitting an
    # extra "package".
    assert blocks[0]["counts"]["upgrade"] == 1


def test_parse_apt_mark_showhold():
    assert parse_apt_mark_showhold(
        "nvidia-driver-470\nlibfoo-dev\n\n"
    ) == ["nvidia-driver-470", "libfoo-dev"]


def test_parse_dpkg_audit():
    text = (
        "The following packages are in a mess due to serious problems "
        "during installation. They must be reinstalled for them "
        "(and any packages that depend on them) to function properly:\n"
        " libfoo-dev\n"
        " libbar1\n"
    )
    assert parse_dpkg_audit(text) == ["libfoo-dev", "libbar1"]


def test_parse_term_log_errors_captures_markers():
    text = (
        "Preparing to unpack ...\n"
        "Setting up libfoo ...\n"
        "E: Sub-process /usr/bin/dpkg returned an error code (1)\n"
        "dpkg: error processing package libbar (--configure):\n"
        " Errors were encountered while processing:\n"
    )
    out = parse_term_log_errors(text)
    assert any("E:" in m for m in out)
    assert any("dpkg: error" in m for m in out)


async def test_collector_writes_facts(tmp_path: Path):
    history = tmp_path / "history.log"
    history.write_text(HISTORY_TEXT)
    term = tmp_path / "term.log"
    term.write_text("E: Sub-process /usr/bin/dpkg returned an error code (1)\n")

    async def fake_run(args):
        if args == ["apt-mark", "showhold"]:
            return 0, "nvidia-driver-470\n"
        if args == ["dpkg", "--audit"]:
            return 0, " libbar1\n"
        return 1, ""

    collector = AptLogCollector(
        history_paths=(history,),
        term_paths=(term,),
        run_command=fake_run,
    )
    result = await collector.collect(T0, T1)
    facts = result.facts or {}
    assert facts["held_packages"] == ["nvidia-driver-470"]
    assert facts["broken_packages"] == ["libbar1"]
    assert any("E:" in line for line in facts["term_log_errors"])
    assert len(facts["recent_transactions"]) == 2
