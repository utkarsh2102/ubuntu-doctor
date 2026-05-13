from __future__ import annotations

import json
from datetime import datetime, timezone

from ubuntu_doctor.collectors.journald.plugin import (
    JournaldCollector,
    parse_apparmor_message,
    parse_journal_jsonl,
)
from ubuntu_doctor.snapshot import EventKind

WINDOW_START = datetime(2026, 5, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 5, 31, tzinfo=timezone.utc)

T0_US = int(datetime(2026, 5, 10, 14, 30, 42, tzinfo=timezone.utc).timestamp() * 1_000_000)


def _audit_line(
    profile: str = "snap.spotify.spotify",
    operation: str = "open",
    name: str = "/home/u/.config/pulse/cookie",
    comm: str = "spotify",
    ts_us: int = T0_US,
    transport: str = "audit",
) -> str:
    message = (
        f'audit: type=1400 audit(1715347822.122:42): '
        f'apparmor="DENIED" operation="{operation}" '
        f'profile="{profile}" name="{name}" pid="12345" '
        f'comm="{comm}" requested_mask="r" denied_mask="r" fsuid="1000" '
        f'ouid="1000"'
    )
    return json.dumps(
        {
            "__CURSOR": "abc",
            "__REALTIME_TIMESTAMP": str(ts_us),
            "_TRANSPORT": transport,
            "_HOSTNAME": "box",
            "MESSAGE": message,
        }
    )


def test_parse_apparmor_message_extracts_fields():
    message = (
        'audit: type=1400 audit(1715347822.122:42): apparmor="DENIED" '
        'operation="open" profile="snap.spotify.spotify" '
        'name="/home/u/.config/pulse/cookie" pid="12345" '
        'comm="spotify" requested_mask="r" denied_mask="r"'
    )
    fields = parse_apparmor_message(message)
    assert fields["apparmor"] == "DENIED"
    assert fields["operation"] == "open"
    assert fields["profile"] == "snap.spotify.spotify"
    assert fields["name"] == "/home/u/.config/pulse/cookie"
    assert fields["comm"] == "spotify"


def test_parse_journal_jsonl_emits_apparmor_event():
    stdout = _audit_line() + "\n"
    events = parse_journal_jsonl(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    e = events[0]
    assert e.kind == EventKind.APPARMOR_DENIED
    assert e.subject == "snap.spotify.spotify"
    assert e.details["operation"] == "open"
    assert e.details["transport"] == "audit"
    assert e.details["comm"] == "spotify"


def test_parse_journal_jsonl_filters_window():
    inside_us = T0_US
    before_us = int(
        datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1_000_000
    )
    after_us = int(
        datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp() * 1_000_000
    )
    stdout = "\n".join(
        [
            _audit_line(profile="p.inside", ts_us=inside_us),
            _audit_line(profile="p.before", ts_us=before_us),
            _audit_line(profile="p.after", ts_us=after_us),
        ]
    )
    events = parse_journal_jsonl(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    assert events[0].subject == "p.inside"


def test_parse_journal_jsonl_ignores_non_apparmor_messages():
    not_apparmor = json.dumps(
        {
            "__REALTIME_TIMESTAMP": str(T0_US),
            "MESSAGE": "Started Some Random Service",
        }
    )
    stdout = not_apparmor + "\n" + _audit_line()
    events = parse_journal_jsonl(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1
    assert events[0].kind == EventKind.APPARMOR_DENIED


def test_parse_journal_jsonl_handles_malformed_lines():
    stdout = (
        "not json at all\n"
        "{}\n"  # JSON but no fields
        "\n"
        + _audit_line()
        + "\n"
    )
    events = parse_journal_jsonl(stdout, WINDOW_START, WINDOW_END)
    assert len(events) == 1


def test_parse_journal_jsonl_handles_unquoted_apparmor_marker():
    # Newer kaudit can emit `apparmor=DENIED` without quotes.
    message = (
        "audit: type=1400 audit(1715347822.122:42): apparmor=DENIED "
        'operation="exec" profile="snap.firefox.firefox" '
        'name="/usr/bin/something" pid="1" comm="x"'
    )
    line = json.dumps(
        {"__REALTIME_TIMESTAMP": str(T0_US), "MESSAGE": message}
    )
    events = parse_journal_jsonl(line + "\n", WINDOW_START, WINDOW_END)
    assert len(events) == 1


async def test_collector_invokes_journalctl_correctly():
    invoked: list[list[str]] = []

    async def fake_run(args):
        invoked.append(args)
        return 0, _audit_line() + "\n"

    collector = JournaldCollector(run_command=fake_run)
    result = await collector.collect(WINDOW_START, WINDOW_END)
    assert result.degradation is None
    assert len(result.events) == 1
    args = invoked[0]
    assert args[0] == "journalctl"
    assert "--grep" in args
    # Must request JSON output for predictable parsing.
    assert "-o" in args and "json" in args


async def test_collector_degrades_on_journalctl_failure():
    async def fake_run(args):
        return 1, ""

    collector = JournaldCollector(run_command=fake_run)
    result = await collector.collect(WINDOW_START, WINDOW_END)
    assert result.events == []
    assert result.degradation is not None
    assert "journal" in (result.degradation.fix_command or "").lower()
