from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ubuntu_doctor.collectors.apparmor_audit.plugin import (
    ApparmorAuditCollector,
    parse_audit_log,
)
from ubuntu_doctor.snapshot import EventKind

WINDOW_START = datetime(2026, 5, 10, tzinfo=timezone.utc)
WINDOW_END = WINDOW_START + timedelta(days=1)

EPOCH = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def _audit_line(epoch: float, profile: str = "snap.spotify.spotify") -> str:
    return (
        f'type=AVC msg=audit({epoch:.3f}:42): apparmor="DENIED" '
        f'operation="open" profile="{profile}" '
        f'name="/home/u/.config/pulse/cookie" pid="12345" '
        f'comm="spotify" requested_mask="r" denied_mask="r"'
    )


def test_parse_audit_log_extracts_apparmor_denials():
    text = _audit_line(EPOCH) + "\n"
    events = parse_audit_log(text, window_start=WINDOW_START, window_end=WINDOW_END)
    assert len(events) == 1
    e = events[0]
    assert e.kind == EventKind.APPARMOR_DENIED
    assert e.subject == "snap.spotify.spotify"
    assert e.details["operation"] == "open"
    assert e.details["audit_serial"] == "42"


def test_parse_audit_log_skips_non_apparmor_lines():
    text = (
        f'type=SYSCALL msg=audit({EPOCH:.3f}:1): syscall=257\n'
        + _audit_line(EPOCH)
        + "\n"
    )
    events = parse_audit_log(text, window_start=WINDOW_START, window_end=WINDOW_END)
    assert len(events) == 1


def test_parse_audit_log_respects_window():
    inside = _audit_line(EPOCH)
    outside_past = _audit_line(EPOCH - 86400 * 7)
    outside_future = _audit_line(EPOCH + 86400 * 7)
    events = parse_audit_log(
        "\n".join([inside, outside_past, outside_future]),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert len(events) == 1


def test_parse_audit_log_handles_unquoted_marker():
    line = (
        f'type=AVC msg=audit({EPOCH:.3f}:7): apparmor=DENIED '
        'operation="exec" profile="snap.firefox.firefox" '
        'name="/usr/bin/bash" pid="1"'
    )
    events = parse_audit_log(line + "\n", window_start=WINDOW_START, window_end=WINDOW_END)
    assert len(events) == 1
    assert events[0].subject == "snap.firefox.firefox"


async def test_collector_returns_no_degradation_when_file_missing(tmp_path: Path):
    # Missing file is the typical desktop case. No degradation, no events.
    result = await ApparmorAuditCollector(
        audit_paths=(tmp_path / "missing.log",)
    ).collect(WINDOW_START, WINDOW_END)
    assert result.events == []
    assert result.degradation is None


async def test_collector_reads_file_when_readable(tmp_path: Path):
    log = tmp_path / "audit.log"
    log.write_text(_audit_line(EPOCH) + "\n")
    result = await ApparmorAuditCollector(audit_paths=(log,)).collect(
        WINDOW_START, WINDOW_END
    )
    assert len(result.events) == 1
    assert result.events[0].subject == "snap.spotify.spotify"


async def test_collector_degrades_on_permission_error(tmp_path: Path):
    log = tmp_path / "audit.log"
    log.write_text(_audit_line(EPOCH))
    log.chmod(0o000)
    try:
        result = await ApparmorAuditCollector(audit_paths=(log,)).collect(
            WINDOW_START, WINDOW_END
        )
        assert result.events == []
        assert result.degradation is not None
        assert "sudo" in (result.degradation.fix_command or "")
    finally:
        log.chmod(0o644)  # so cleanup can delete it
