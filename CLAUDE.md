# ubuntu-doctor

CLI that diagnoses Ubuntu system problems. Collects evidence from journald,
dpkg/apt, snap, dmesg, AppArmor, hardware, disk, etc., correlates them
**deterministically**, and uses a **local** LLM to explain the top hypotheses
in plain English.

The full design is in [docs/plan.md](docs/plan.md). Read it before making
architectural changes.

## Architecture (one screen)

```
CLI → Orchestrator → Collectors (parallel, async)
                  ↓
                Snapshot (typed timeline + facts + degradation reports)
                  ↓
              Analyzers (parallel, rule-based) → [Hypothesis, ...]
                  ↓
                Ranker (uses prior outcomes from local incident memory)
                  ↓
              LLM (single call by default; structured JSON in/out)
                  ↓
                Renderer (text or JSON)
```

- **Collectors** live in [src/ubuntu_doctor/collectors/<source>/](src/ubuntu_doctor/collectors/) —
  one folder per data source. Each is async, pure, has a degradation mode,
  exposes `COLLECTOR = MySourceCollector()` from `plugin.py`.
- **Analyzers** live in [src/ubuntu_doctor/analyzers/<rule>/](src/ubuntu_doctor/analyzers/) —
  rule-based correlators that consume a `Snapshot` and emit `Hypothesis`
  objects with evidence pointers, suggested commands (as text), and risks.
- **LLM** is reached over OpenAI-compatible HTTP at
  `http://localhost:8336/v1` (Ubuntu Inference Snap). Default model:
  `gemma:e4b` (the local endpoint accepts either `gemma:e4b` or the
  canonical id `gemma4-e4b-q4-k-m` — both route to the same loaded
  model). Endpoint and model are config knobs (`--base-url`, `--model`).

## Hard rules

- **Read-only.** No collector and no analyzer ever writes to the system.
  The LLM never executes commands. Fixes are rendered as copy-pasteable
  text, never run.
- **No `--apply` in v1.** Don't add it until v2; when added, it must
  re-prompt per command with diff/effect.
- **Never require root for basic operation.** Collectors that can't read
  a source emit a `DegradationReport` carrying the exact sudo command
  that would unlock it. The summary surfaces these — no silent omission.
- **One LLM call per `doctor` run by default.** `--deep` allows
  follow-up calls. Multi-agent fan-out per plugin is explicitly not the
  design — local inference serialises requests, so N agents = N × latency
  with no accuracy gain.
- **Rules first, LLM second.** A deterministic correlator produces the
  candidate set. The LLM ranks/explains; it does not invent hypotheses.
  `doctor --no-ai` must remain useful on its own.
- **No generic man-page / `/usr/share/doc` RAG.** Retrieval is
  event-driven: changelogs, NEWS, AppArmor profile diffs, apport reports,
  kernel taint, local incident memory. Scoped to the incident window.

## Layout

```
src/ubuntu_doctor/
├── cli.py                # argparse, subcommands
├── orchestrator.py       # async collector + analyzer fan-out
├── snapshot.py           # TimelineEvent, Snapshot, Hypothesis, DegradationReport
├── collectors/<source>/  # plugin = one data source
│   └── plugin.py         # exposes COLLECTOR
├── analyzers/<rule>/     # plugin = one correlation rule
│   └── plugin.py         # exposes ANALYZER
├── llm/                  # OpenAI-compatible client, prompts, retrieval (TBD)
├── feedback/             # local incident memory: SQLite + vector (TBD)
└── ui/                   # text and JSON renderers
```

## Development

```bash
# install in editable mode with dev deps
pip install -e '.[dev]'

# run the CLI (currently --no-ai only)
doctor --no-ai
doctor --no-ai --json
doctor --no-ai --since 7d

# run tests
pytest -q
```

## Status

- v1 in progress. Landed so far:
  - Collectors: `dpkg_history`, `systemd_failed`, `dmesg`, `journald`
  - Analyzers: `postupgrade_regression`, `systemd_health`,
    `apparmor_denials`
  - LLM client against the Ubuntu Inference Snap with lenient JSON
    parsing, graceful degradation when the endpoint is unreachable,
    and a hard safety filter (`FORBIDDEN_FIX_PATTERNS`) that strips
    `aa-complain`/`aa-disable`/`rm -rf /`/`dd of=/dev/...` etc. from
    model-proposed fix_commands and surfaces the strip as a risk
  - Symptom-keyword [ranker.py](src/ubuntu_doctor/ranker.py) that
    re-ranks deterministic hypotheses for `doctor why <symptom>`
  - [RAG layer](src/ubuntu_doctor/rag/) that retrieves changelog
    entries between two versions, NEWS files, AppArmor profile bodies
    from `/etc/apparmor.d` and `/var/lib/snapd/apparmor/profiles`, and
    apport reports matched by ExecutablePath. Scoped per-hypothesis,
    deduplicated across hypotheses, capped at ~2KB/snippet.
  - [Feedback store](src/ubuntu_doctor/feedback/) — SQLite-backed local
    incident memory at `~/.local/share/ubuntu-doctor/incidents.db`.
    Fingerprint is a set of canonical tokens (analyzer ids, event
    kinds, subjects); similarity is Jaccard, not vector — no extra
    dependency. Past incidents above 0.2 similarity are injected into
    the LLM prompt as few-shot context. `~/.cache/ubuntu-doctor/last_run.json`
    cache carries the most recent diagnosis so `doctor feedback` knows
    what to talk about.
  - CLI: `doctor` (passive), `doctor why <symptom>` (active), and
    `doctor feedback` (interactive incident recorder). All accept
    `--no-ai`, `--json`, `--since`, `--model`, `--base-url`,
    `--llm-timeout`; diagnose modes additionally accept `--no-rag` and
    `--no-history` for opt-out of the new subsystems.
- Not yet implemented: the remaining analyzers and collectors
  (`apt_log`, `snap_changes`, `hardware`, `cache_state`, `diskspace`,
  and analyzers `held_packages`, `firmware_mismatch`, `oom_attribution`,
  `snap_refresh_breakage`, `irq_driver_regression`, `cache_health`).
  See [docs/plan.md](docs/plan.md) for the full backlog.

**Window semantics:** `--since` filters historical events (package
upgrades, past service failures). It does NOT filter
*current-state facts* like "this unit is currently failed" — those are
emitted regardless of when they last exited. The `systemd_failed`
collector documents this explicitly; new state-fact collectors should
follow the same pattern.
