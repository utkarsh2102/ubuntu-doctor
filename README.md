# ubuntu-doctor

> `git blame` for your broken Ubuntu system.

A CLI that diagnoses Ubuntu system problems and explains them in plain English.
It builds a timeline of recent system changes — package upgrades, service failures,
kernel events, AppArmor denials — correlates them deterministically, and uses a
**local** LLM to explain the most likely causes.

```
ubuntu-doctor — window: 2026-05-06T… → 2026-05-13T…
  Collected 47 events from 4 sources

ubuntu-doctor — diagnosis (model: gemma:e4b)
  pulseaudio.service has been restarting repeatedly since linux-firmware
  was upgraded two days ago. The firmware upgrade likely destabilised the
  ALSA device enumeration path.

Top hypotheses:

  [1] pulseaudio.service failed shortly after linux-firmware was upgraded
      (LLM confidence 0.87)
      …
      suggested fix commands (NOT executed):
        $ sudo apt install linux-firmware=20240318.git3b128b60-0ubuntu1
        $ sudo apt-mark hold linux-firmware
        $ sudo systemctl restart pulseaudio.service
```

**ubuntu-doctor is read-only.** Suggested fixes are rendered as copy-pasteable
commands — nothing is ever executed without you.

---

## Why this exists

When Ubuntu breaks, the information you need is scattered across a dozen places:
`journalctl`, `dmesg`, `/var/log/apt/history.log`, `snap changes`, `dpkg -l`,
AppArmor audit logs. The problem is not that the logs don't exist — it's that
correlating them is tedious, and the symptom you observe (audio gone, Wi-Fi dead,
network slow) is rarely in the same place as the cause (kernel/driver mismatch,
held package, missing firmware, AppArmor policy change).

`ubuntu-doctor` does that correlation for you.

### Real situations this would have helped

**Post-upgrade audio silence** — user runs `apt upgrade`, reboots, no sound.
The actual cause (nvidia driver held back, destabilising PulseAudio's device
enumeration) is not visible anywhere obvious. `doctor` sees: kernel upgraded,
nvidia held, pulseaudio crashed repeatedly. Connects them.

**Mystery network drop on a cloud VM** — routine `unattended-upgrade` run causes
intermittent packet drops. `irqbalance` was upgraded and its config changed IRQ
affinity for the NIC. `doctor` sees: `irqbalance` upgraded, network IRQ errors
in dmesg at the same time. Surfaces the correlation. Points to the config diff.

**Snap app silently stopped working** — Spotify snap stopped launching after a
system update. An AppArmor policy update now denies `~/.config/pulse`. No error,
no notification. `doctor` sees: AppArmor denial for the snap, correlated with a
snapd refresh. Explains the denial. Suggests the right `snap connect` command.

**2am OOM crisis** — a production server throws OOM errors, services restart
randomly. `doctor --deep` gathers memory pressure events, correlates with
restarting services, ranks candidates by likelihood.

**New laptop Wi-Fi regression** — Wi-Fi works, then stops after first
`apt upgrade`. `linux-firmware` was upgraded; the new firmware for a specific
Realtek card has a regression. `doctor` sees: firmware upgraded, Wi-Fi dmesg
errors starting at next boot, hardware ID of the card.

---

## Install

Requires Python 3.12+.

```bash
git clone <this-repo> ubuntu-doctor
cd ubuntu-doctor

python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The editable install registers a `doctor` console script inside the venv.
Activate the venv or prefix with `.venv/bin/`:

```bash
source .venv/bin/activate
doctor --help
```

### LLM (Ubuntu Inference Snap)

By default `doctor` calls a **local** LLM at `http://localhost:8336/v1` — the
[Ubuntu Inference Snap](https://snapcraft.io/ubuntu-ai) running `gemma:e4b`.
No API key is required; nothing leaves the machine.

```bash
sudo snap install ubuntu-ai
# the snap starts an OpenAI-compatible endpoint at localhost:8336
```

If the Inference Snap is not installed, `doctor` automatically falls back to
deterministic-only mode and tells you what it couldn't do. You can also pass any
other OpenAI-compatible endpoint with `--base-url`.

---

## Usage

```
doctor                        # passive: what is wrong right now?
doctor why <symptom>          # active: why did my audio stop working?

doctor --no-ai                # skip LLM; deterministic rules only
doctor --json                 # machine-readable JSON output
doctor --since 7d             # limit the analysis window (default: 14d)
doctor --deep                 # allow per-analyzer follow-up LLM calls

doctor --model <name>         # override the LLM model (default: gemma:e4b)
doctor --base-url <url>       # override the inference endpoint
doctor --llm-timeout <secs>   # seconds to wait for the LLM (default: 120)
```

### Time window

`--since` controls how far back *historical* events (package upgrades, past
service failures) are searched. Current-state facts — "this unit is currently
failed right now" — are always reported regardless of when they last changed.

```bash
doctor --since 2d             # look back 2 days
doctor --since 6h             # last 6 hours
doctor --since 30m            # last 30 minutes
```

### Passive diagnosis

```bash
doctor
```

Runs all collectors in parallel, builds a timeline, runs all analyzers, and asks
the LLM to explain the top correlations.

### Active symptom-directed diagnosis

```bash
doctor why "audio stopped working after the update"
doctor why "wifi drops randomly"
doctor why "snap app won't open"
```

The symptom phrase is used to re-rank hypotheses before the LLM call, boosting
findings that relate to the subsystem mentioned (audio, network, display,
bluetooth, snap, memory, etc.).

### No-AI mode

```bash
doctor --no-ai
doctor why "audio gone" --no-ai
```

Produces the deterministic rule findings without calling the LLM. Useful when
the Inference Snap is not installed, when you're on a slow machine, or when you
want to see the raw analyzer output before LLM re-ranking.

---

## How it works

```
CLI → Orchestrator → Collectors (parallel, async)
                  ↓
                Snapshot (typed timeline + facts + degradation reports)
                  ↓
              Analyzers (parallel, rule-based) → [Hypothesis, ...]
                  ↓
                Ranker (symptom keyword boost for `doctor why`)
                  ↓
              LLM (single call; structured JSON in/out)
                  ↓
                Renderer (text or JSON)
```

### Collectors

Each collector reads one data source and returns typed `TimelineEvent` objects
plus an optional `DegradationReport` if data was unavailable (permission denied,
missing tool, etc.). All collectors run in parallel under a 3-second budget.

| Collector | Source | Events emitted |
|---|---|---|
| `dpkg_history` | `/var/log/dpkg.log` | Package installs, upgrades, removals, purges |
| `systemd_failed` | `systemctl --failed` | Currently-failed systemd units (with Result/LoadState) |
| `dmesg` | `journalctl --dmesg` | OOM kills, kernel taints, firmware load failures, hardware errors (ATA, NVMe, USB, PCIe AER, CPU lockups) |
| `journald` | `journalctl --grep apparmor` | AppArmor denial events (parsed audit fields) |

### Analyzers

Analyzers consume the merged `Snapshot` and emit `Hypothesis` objects with a
title, confidence score, rationale, evidence pointers, suggested fix commands
(never executed), investigation steps, and risks.

| Analyzer | What it detects |
|---|---|
| `postupgrade_regression` | Package upgrades correlated with service failures via temporal proximity and package-to-unit name heuristics |
| `systemd_health` | Failed units classified by systemd's `Result` (oom-kill, core-dump, timeout, signal, masked, not-found) plus subsystem cluster detection when multiple related units fail together |
| `apparmor_denials` | AppArmor denials grouped by profile, boosted when an apparmor-related package or snapd was upgraded in the same window |

### LLM call

`doctor` makes **one structured JSON call** per run by default. The prompt
contains the top-ranked hypotheses, event timeline, and optional symptom; the
model returns a plain-English summary, re-ranked hypotheses with confidence
scores, suggested fix commands, and a "what I did not check" note.

The LLM has **no tool access**. It receives read-only context. Any commands it
suggests are rendered as copy-pasteable text; `ubuntu-doctor` never executes
them.

JSON parsing is lenient: the client strips markdown fences, extracts the
outermost `{...}` block, and validates required fields. Any failure mode
(connection refused, timeout, non-200, malformed JSON, hallucinated hypothesis
IDs) is surfaced as a degradation message and the tool falls back to
deterministic output.

### Privilege and degradation

`doctor` runs without root by default. Collectors that need elevated access
emit a `DegradationReport` with the exact `sudo` command that would unlock more
data — nothing is silently omitted. A "What I couldn't see" section in the
output lists every degradation with its unlock command.

```
What I couldn't see:
  - dmesg: `journalctl --dmesg` exited 1
      to unlock: sudo journalctl --dmesg
```

---

## Output formats

### Text (default)

Human-readable terminal output with the LLM summary at the top and the
deterministic findings below. When `--no-ai` is used or the LLM is unreachable,
only the deterministic section is shown.

### JSON (`--json`)

Machine-readable. Useful for piping into other tools, dashboards, or automated
workflows. The schema includes `snapshot`, `hypotheses`, and (when available)
`explanation`.

---

## Development

```bash
# install in editable mode with dev deps
pip install -e '.[dev]'

# run the CLI
doctor --no-ai
doctor --no-ai --json
doctor --no-ai --since 7d
doctor why "audio gone" --no-ai

# run tests
pytest -q
```

### Project layout

```
src/ubuntu_doctor/
├── cli.py                     # argparse, subcommands
├── orchestrator.py            # async collector + analyzer fan-out
├── snapshot.py                # TimelineEvent, Snapshot, Hypothesis, DegradationReport
├── ranker.py                  # symptom keyword re-ranker
├── collectors/<source>/       # one folder per data source
│   └── plugin.py              # exposes COLLECTOR
├── analyzers/<rule>/          # one folder per correlation rule
│   └── plugin.py              # exposes ANALYZER
├── llm/                       # OpenAI-compatible client + prompts
└── ui/                        # text and JSON renderers
```

Each collector and analyzer is a self-contained plugin. New ones can be added
without touching any existing code — see `collectors/base.py` and
`analyzers/base.py` for the ABCs.

---

## Design decisions

**Rules first, LLM explains.** A deterministic correlator produces the candidate
set. The LLM re-ranks and explains in plain English; it does not invent
hypotheses. `doctor --no-ai` must remain useful on its own.

**One LLM call per run.** The local Inference Snap serialises requests; N agents
= N × latency with no accuracy gain. `--deep` is the escape hatch for
per-analyzer follow-up calls on low-confidence cases.

**No generic man-page RAG.** Retrieval is event-driven: changelogs, NEWS,
AppArmor profile diffs, apport reports, kernel taint flags, local incident
memory — all scoped to packages and events in the incident window.

**Privacy by default.** All data stays on the machine. The model runs in the
Ubuntu Inference Snap. There is no telemetry, no upload, no cloud call.

---

## Status

Pre-alpha — v1 vertical slice in progress.

Implemented:
- Collectors: `dpkg_history`, `systemd_failed`, `dmesg`, `journald`
- Analyzers: `postupgrade_regression`, `systemd_health`, `apparmor_denials`
- LLM client against the Ubuntu Inference Snap with lenient JSON parsing and
  graceful degradation
- Symptom-keyword ranker for `doctor why <symptom>`
- CLI: `doctor` and `doctor why <symptom>`, both accepting `--no-ai`, `--json`,
  `--since`, `--model`, `--base-url`, `--llm-timeout`

Not yet implemented:
- RAG retrieval (changelogs, NEWS, incident memory)
- Feedback / local incident memory store
- Collectors: `apt_log`, `snap_changes`, `hardware`, `cache_state`, `diskspace`
- Analyzers: `held_packages`, `firmware_mismatch`, `oom_attribution`,
  `snap_refresh_breakage`, `irq_driver_regression`, `cache_health`
- `doctor explain <hypothesis-id>` subcommand
- `doctor feedback` subcommand and `--deep` follow-up LLM calls

See [docs/plan.md](docs/plan.md) for the full design and backlog.

---

## License

GPL-3.0-or-later. See [pyproject.toml](pyproject.toml) for authorship.
