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
  `gemma4:e4b`. Endpoint + model are config knobs.

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

- v1 vertical slice in progress: `dpkg_history` + `systemd_failed`
  collectors → `postupgrade_regression` analyzer → text/JSON renderer.
  CLI supports `--no-ai`, `--json`, `--since`.
- LLM client, RAG retrieval, feedback store, the remaining six
  analyzers, and the remaining seven collectors are not yet implemented.
  See [docs/plan.md](docs/plan.md) for the full backlog.
