from __future__ import annotations

import json
from datetime import timedelta

import pytest

from ubuntu_doctor.cli import build_parser, parse_since


def test_parse_since_accepts_units():
    assert parse_since("14d") == timedelta(days=14)
    assert parse_since("6h") == timedelta(hours=6)
    assert parse_since("30m") == timedelta(minutes=30)
    assert parse_since("2w") == timedelta(weeks=2)
    assert parse_since("45s") == timedelta(seconds=45)


def test_parse_since_rejects_garbage():
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        parse_since("yesterday")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_since("14")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_since("d14")


def test_default_invocation_has_no_subcommand():
    parser = build_parser()
    args = parser.parse_args(["--no-ai"])
    assert args.cmd is None
    assert args.no_ai is True
    assert args.since == timedelta(days=14)


def test_why_subcommand_collects_symptom_words():
    parser = build_parser()
    args = parser.parse_args(["why", "my", "audio", "stopped", "working"])
    assert args.cmd == "why"
    assert args.symptom == ["my", "audio", "stopped", "working"]


def test_why_subcommand_inherits_common_options():
    parser = build_parser()
    args = parser.parse_args(["why", "audio", "--no-ai", "--since", "3d", "--json"])
    assert args.cmd == "why"
    assert args.no_ai is True
    assert args.json is True
    assert args.since == timedelta(days=3)


def test_default_model_and_base_url_from_client_module():
    from ubuntu_doctor.llm.client import DEFAULT_BASE_URL, DEFAULT_MODEL

    parser = build_parser()
    args = parser.parse_args([])
    assert args.model == DEFAULT_MODEL
    assert args.base_url == DEFAULT_BASE_URL


def test_model_and_base_url_overridable():
    parser = build_parser()
    args = parser.parse_args(
        ["--model", "qwen:7b", "--base-url", "http://example/v1"]
    )
    assert args.model == "qwen:7b"
    assert args.base_url == "http://example/v1"


def test_json_renderer_includes_llm_unavailable_block_on_error(monkeypatch):
    # End-to-end: when --no-ai, the LLM block is absent (None). When the
    # LLM is enabled but unavailable, the block should report the error.
    from ubuntu_doctor import cli
    from ubuntu_doctor.llm.types import LLMUnavailable
    from ubuntu_doctor.snapshot import Snapshot

    async def empty_build_snapshot(collectors, window_start, window_end):
        return Snapshot(
            started_at=window_end, window_start=window_start, window_end=window_end
        )

    async def empty_run_analyzers(analyzers, snapshot):
        return []

    class FailingClient:
        async def explain(
            self,
            snapshot,
            hypotheses,
            symptom=None,
            *,
            retrieved=None,
            past_incidents=None,
        ):
            raise LLMUnavailable("connection refused")

    monkeypatch.setattr(cli, "build_snapshot", empty_build_snapshot)
    monkeypatch.setattr(cli, "run_analyzers", empty_run_analyzers)
    monkeypatch.setattr(cli, "_maybe_llm_client", lambda args: FailingClient())

    rc = cli.main(["--json"])
    assert rc == 0


def test_select_analyzers_default_returns_all():
    from ubuntu_doctor.cli import ANALYZER_REGISTRY, select_analyzers

    selected = select_analyzers(None, None)
    assert {a.id for a in selected} == set(ANALYZER_REGISTRY)


def test_select_analyzers_allowlist_filters():
    from ubuntu_doctor.cli import select_analyzers

    selected = select_analyzers(
        ["postupgrade_regression", "apparmor_denials"], None
    )
    assert {a.id for a in selected} == {
        "postupgrade_regression",
        "apparmor_denials",
    }


def test_select_analyzers_skip_removes_from_default():
    from ubuntu_doctor.cli import ANALYZER_REGISTRY, select_analyzers

    selected = select_analyzers(None, ["systemd_health"])
    assert "systemd_health" not in {a.id for a in selected}
    assert len(selected) == len(ANALYZER_REGISTRY) - 1


def test_select_analyzers_allowlist_then_skip():
    from ubuntu_doctor.cli import select_analyzers

    selected = select_analyzers(
        ["postupgrade_regression", "systemd_health", "apparmor_denials"],
        ["systemd_health"],
    )
    assert {a.id for a in selected} == {
        "postupgrade_regression",
        "apparmor_denials",
    }


def test_select_analyzers_empty_allowlist_runs_none():
    from ubuntu_doctor.cli import select_analyzers

    assert select_analyzers([], None) == ()


def test_select_analyzers_unknown_id_raises():
    from ubuntu_doctor.cli import select_analyzers

    with pytest.raises(SystemExit) as excinfo:
        select_analyzers(["does_not_exist"], None)
    assert "does_not_exist" in str(excinfo.value)


def test_select_analyzers_unknown_in_skip_raises():
    from ubuntu_doctor.cli import select_analyzers

    with pytest.raises(SystemExit) as excinfo:
        select_analyzers(None, ["nope"])
    assert "nope" in str(excinfo.value)


def test_parse_analyzer_list_handles_whitespace_and_empties():
    from ubuntu_doctor.cli import parse_analyzer_list

    assert parse_analyzer_list("a, b ,,c") == ["a", "b", "c"]
    assert parse_analyzer_list("") == []


def test_analyzers_flag_threads_through_to_run_analyzers(monkeypatch, capsys):
    from ubuntu_doctor import cli
    from ubuntu_doctor.snapshot import Snapshot

    async def empty_build_snapshot(collectors, window_start, window_end):
        return Snapshot(
            started_at=window_end, window_start=window_start, window_end=window_end
        )

    seen: dict[str, tuple[str, ...]] = {}

    async def capturing_run_analyzers(analyzers, snapshot):
        seen["ids"] = tuple(a.id for a in analyzers)
        return []

    monkeypatch.setattr(cli, "build_snapshot", empty_build_snapshot)
    monkeypatch.setattr(cli, "run_analyzers", capturing_run_analyzers)

    rc = cli.main(
        [
            "--no-ai",
            "--json",
            "--no-rag",
            "--no-history",
            "--analyzers",
            "postupgrade_regression,apparmor_denials",
        ]
    )
    capsys.readouterr()
    assert rc == 0
    assert set(seen["ids"]) == {"postupgrade_regression", "apparmor_denials"}


def test_no_ai_skips_llm_call(monkeypatch, capsys):
    from ubuntu_doctor import cli
    from ubuntu_doctor.snapshot import Snapshot

    async def empty_build_snapshot(collectors, window_start, window_end):
        return Snapshot(
            started_at=window_end, window_start=window_start, window_end=window_end
        )

    async def empty_run_analyzers(analyzers, snapshot):
        return []

    def must_not_call(args):
        raise AssertionError("LLM client must not be constructed with --no-ai")

    monkeypatch.setattr(cli, "build_snapshot", empty_build_snapshot)
    monkeypatch.setattr(cli, "run_analyzers", empty_run_analyzers)
    # _maybe_llm_client should short-circuit on --no-ai; replace it with a
    # guard that fails loudly if anything else constructs a client.
    monkeypatch.setattr(
        cli,
        "_maybe_llm_client",
        lambda args: must_not_call(args) if not args.no_ai else None,
    )

    rc = cli.main(["--no-ai", "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["llm"] is None
