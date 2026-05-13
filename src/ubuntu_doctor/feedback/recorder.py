"""Interactive `doctor feedback` flow.

Reads the last-run cache, presents the diagnoses the user just saw,
prompts them for which hypothesis (if any) was the cause, what they
ran, what happened, and the outcome flag, then writes an `Incident`
to the local store.

Designed to fail gracefully:
- If there's no last-run cache, we say so and exit non-zero.
- If the user pipes `/dev/null` in (or hits ^D), we exit cleanly
  without writing anything.

All prompts go to the injected stderr stream (defaults to sys.stderr).
That keeps stdout clean for callers piping the program elsewhere AND
keeps the recorder testable — tests can capture prompts and error
messages via a StringIO.
"""

from __future__ import annotations

import sys
from typing import Callable, TextIO

from ubuntu_doctor.feedback.lastrun import LastRun, LastRunCache
from ubuntu_doctor.feedback.store import OUTCOME_VALUES, Incident, IncidentStore

InputFn = Callable[[str], str]


def _make_default_input(err: TextIO) -> InputFn:
    def _input(prompt: str) -> str:
        err.write(prompt)
        err.flush()
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    return _input


def _print(stream: TextIO, *values: object) -> None:
    print(*values, file=stream)


def _read_lines_until_blank(
    input_fn: InputFn, prompt: str, err: TextIO
) -> str:
    err.write(prompt + " (end with a blank line)\n")
    err.flush()
    lines: list[str] = []
    while True:
        try:
            line = input_fn("    > ")
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return "\n".join(lines)


def _prompt_outcome(input_fn: InputFn, err: TextIO) -> str:
    options = ", ".join(OUTCOME_VALUES)
    while True:
        try:
            raw = input_fn(f"  outcome [{options}] (default unknown): ")
        except EOFError:
            return "unknown"
        raw = raw.strip().lower()
        if not raw:
            return "unknown"
        if raw in OUTCOME_VALUES:
            return raw
        err.write(f"  not one of: {options}\n")


def record_feedback(
    *,
    cache: LastRunCache | None = None,
    store: IncidentStore | None = None,
    input_fn: InputFn | None = None,
    stderr: TextIO | None = None,
) -> tuple[int, int | None]:
    """Run the interactive feedback flow.

    Returns `(exit_code, incident_id)`. `incident_id` is None when no
    feedback was recorded (no cache, user bailed, etc.).
    """
    cache = cache or LastRunCache()
    store = store or IncidentStore()
    err = stderr or sys.stderr
    input_fn = input_fn or _make_default_input(err)

    last = cache.read()
    if last is None:
        _print(
            err,
            f"No previous diagnosis found at {cache.path}.",
            "Run `doctor` first, then `doctor feedback`.",
        )
        return 1, None

    _show_diagnosis(last, err)
    chosen_id = _prompt_chosen_hypothesis(last, input_fn, err)
    suggested = _suggested_commands_for(last, chosen_id)

    try:
        applied = _read_lines_until_blank(
            input_fn, "Commands you actually ran:", err
        )
        observed = _read_lines_until_blank(
            input_fn, "What happened after:", err
        )
        outcome = _prompt_outcome(input_fn, err)
        notes = _read_lines_until_blank(
            input_fn, "Anything else worth noting (optional):", err
        )
    except EOFError:
        _print(err, "Aborted; nothing saved.")
        return 130, None

    incident = Incident(
        fingerprint=last.fingerprint,
        chosen_hypothesis_ids=[chosen_id] if chosen_id else [],
        suggested_fix_commands=suggested,
        applied_commands=[line for line in applied.splitlines() if line],
        observed_effect=observed,
        outcome=outcome,
        notes=notes,
    )
    incident_id = store.save(incident)
    _print(err, f"Saved feedback as incident #{incident_id}.")
    return 0, incident_id


def _show_diagnosis(last: LastRun, err: TextIO) -> None:
    _print(err, f"Last diagnosis was at {last.ts}.")
    if last.symptom:
        _print(err, f"Symptom: {last.symptom!r}")
    if not last.hypotheses:
        _print(err, "(no hypotheses were recorded)")
        return
    _print(err, "Hypotheses (top of the last run):")
    for i, h in enumerate(last.hypotheses, 1):
        _print(err, f"  [{i}] {h.title}  ({h.analyzer})")


def _prompt_chosen_hypothesis(
    last: LastRun, input_fn: InputFn, err: TextIO
) -> str:
    if not last.hypotheses:
        return ""
    while True:
        try:
            raw = input_fn(
                f"Which hypothesis was the cause? "
                f"[1-{len(last.hypotheses)}, `none`, or skip]: "
            )
        except EOFError:
            return ""
        raw = raw.strip().lower()
        if not raw or raw == "skip":
            return ""
        if raw == "none":
            return ""
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(last.hypotheses):
                return last.hypotheses[idx - 1].id
        _print(err, "  please enter a number from the list, 'none', or 'skip'")


def _suggested_commands_for(last: LastRun, chosen_id: str) -> list[str]:
    if not chosen_id:
        return []
    for h in last.hypotheses:
        if h.id == chosen_id:
            return list(h.fix_commands)
    return []
