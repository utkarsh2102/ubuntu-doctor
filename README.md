# ubuntu-doctor

> `git blame` for your broken Ubuntu system.

A CLI that diagnoses Ubuntu system problems and explains them in plain English.
It builds a timeline of recent system changes — package upgrades, service
failures, kernel events, AppArmor denials, snap refreshes, disk and cache
state — correlates them deterministically, retrieves relevant changelogs and
past incidents, and uses a **local** LLM to explain the most likely causes.

```
ubuntu-doctor — window: 2026-05-06T… → 2026-05-13T…
  Collected 47 events from 8 sources

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
commands — nothing is ever executed without you. The LLM has no tool access:
it receives a structured snapshot and returns text.

[live-demo.webm](https://github.com/user-attachments/assets/a6468793-71d2-473f-a308-0191e598cced)

---

## Why this exists

When Ubuntu breaks, the information you need is scattered across a dozen
places: `journalctl`, `dmesg`, `/var/log/apt/history.log`, `snap changes`,
`dpkg -l`, AppArmor audit logs, `/var/crash`. The problem is not that the
logs don't exist — it's that correlating them is tedious, and the symptom
you observe (audio gone, Wi-Fi dead, network slow) is rarely in the same
place as the cause (kernel/driver mismatch, held package, missing firmware,
AppArmor policy change, snap refresh).

`ubuntu-doctor` does that correlation for you.

### Real situations this would have helped

**Post-upgrade audio silence** — `apt upgrade`, reboot, no sound. Cause:
nvidia held back, destabilising PulseAudio. `ubuntu-doctor` sees: kernel upgraded,
nvidia held, pulseaudio crashed repeatedly. Connects them.

**Mystery network drop on a cloud VM** — `unattended-upgrade` runs,
intermittent packet drops follow. `irqbalance` was upgraded and changed IRQ
affinity for the NIC. `ubuntu-doctor` sees: `irqbalance` upgraded, NIC errors in
dmesg at the same time. Surfaces the correlation.

**Snap app silently stopped working** — Spotify snap stopped launching. An
AppArmor policy update now denies `~/.config/pulse`. No error, no
notification. `ubuntu-doctor` sees: AppArmor denial for `snap.spotify.*`,
correlated with a snapd refresh. Suggests the right `snap connect` command.

**2am OOM crisis** — production server throws OOM errors, services restart
randomly. `ubuntu-doctor` groups OOM kills by killed process, ranks repeat
offenders, correlates with recent installs.

**New laptop Wi-Fi regression** — Wi-Fi works, then stops after first
`apt upgrade`. `linux-firmware` was upgraded; new firmware for a specific
Realtek card has a regression. `ubuntu-doctor` sees: firmware upgraded, Wi-Fi dmesg
errors at next boot, exact PCI/USB IDs of the affected card.

---

## Install

Requires Python 3.12+.

### From PyPI

```bash
pip3 install ubuntu-doctor
ubuntu-doctor --help
```

If `pip3 install` complains about an externally-managed environment on
recent Ubuntu releases, either use a venv (below) or pass `--user`:

```bash
pip3 install --user ubuntu-doctor
```

### From source (development)

```bash
git clone https://github.com/utkarsh2102/ubuntu-doctor.git
cd ubuntu-doctor

python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The editable install registers a `ubuntu-doctor` console script inside the venv.
Activate the venv or prefix with `.venv/bin/`:

```bash
source .venv/bin/activate
ubuntu-doctor --help
```

### LLM (Ubuntu Inference Snap)

By default `ubuntu-doctor` calls a **local** LLM at `http://localhost:8336/v1` —
served by a [Canonical Inference Snap](https://documentation.ubuntu.com/inference-snaps/reference/snaps/).

The default snap is `gemma4`, which exposes the `gemma:e4b` model over an
HTTP endpoint. No API key is required; nothing leaves the machine.

```bash
sudo snap install gemma4
# the snap starts an OpenAI-compatible endpoint at localhost:8336
```

If no Inference Snap is installed, `ubuntu-doctor` falls back to deterministic-only
mode and tells you what it couldn't do. You can also point at any other
OpenAI-compatible endpoint via `--base-url` and pick a different model with
`--model`.

---

## Usage

```
ubuntu-doctor                        # passive: what is wrong right now?
ubuntu-doctor why <symptom>          # active: why did my audio stop working?
ubuntu-doctor feedback               # record outcome of the most recent diagnosis

# scope
ubuntu-doctor --since 7d             # window for historical events (default: 14d)

# turning things off
ubuntu-doctor --no-ai                # skip the LLM call
ubuntu-doctor --no-rag               # skip changelog / profile / apport retrieval
ubuntu-doctor --no-history           # don't read or write the local feedback store

# selecting analyzers
ubuntu-doctor --analyzers postupgrade_regression,apparmor_denials
ubuntu-doctor --skip-analyzers cache_health,oom_attribution

# LLM endpoint
ubuntu-doctor --model gemma:e4b
ubuntu-doctor --base-url http://localhost:8336/v1
ubuntu-doctor --llm-timeout 120

# output
ubuntu-doctor --json                 # machine-readable JSON
```

### Time window

`--since` controls how far back *historical* events (package upgrades, past
service failures, snap refreshes) are searched. Current-state facts — "this
unit is currently failed right now", "held packages", "/boot is 92% full" —
are reported regardless of when they last changed.

```bash
ubuntu-doctor --since 2d
ubuntu-doctor --since 6h
ubuntu-doctor --since 30m
```

### Passive diagnosis

```bash
ubuntu-doctor
```

Runs all collectors in parallel, builds a timeline, runs all analyzers,
retrieves relevant snippets (changelogs, AppArmor profiles, apport
reports), finds similar past incidents, and asks the LLM to explain the
top correlations.

### Active symptom-directed diagnosis

```bash
ubuntu-doctor why "audio stopped working after the update"
ubuntu-doctor why "wifi drops randomly"
ubuntu-doctor why "snap app won't open"
```

The symptom phrase is used to re-rank hypotheses before the LLM call,
boosting findings that relate to the named subsystem (audio, network,
display, bluetooth, snap, memory, etc.).

### Selecting analyzers

`--analyzers` is an allowlist; `--skip-analyzers` is a denylist applied
after. Available ids: `apparmor_denials`, `cache_health`,
`firmware_mismatch`, `held_packages`, `irq_driver_regression`,
`oom_attribution`, `postupgrade_regression`, `snap_refresh_breakage`,
`systemd_health`. Unknown ids are a hard error so a typo never silently
falls back to "run everything".

```bash
ubuntu-doctor --analyzers postupgrade_regression,firmware_mismatch
ubuntu-doctor --skip-analyzers cache_health
ubuntu-doctor why "wifi flaky" --analyzers firmware_mismatch,irq_driver_regression
```

### No-AI mode

```bash
ubuntu-doctor --no-ai
ubuntu-doctor why "audio gone" --no-ai
```

Produces the deterministic rule findings without calling the LLM. Useful
when the Inference Snap is not installed, when you're on a slow machine,
or when you want to see the raw analyzer output before LLM re-ranking.

### Feedback

After a `ubuntu-doctor` run, the top hypotheses and a fingerprint are written to
`~/.cache/ubuntu-doctor/last_run.json`. Running `ubuntu-doctor feedback` reads
that file, prompts you for which hypothesis (if any) was the cause, what
you ran, what happened, and an outcome flag, and writes an `Incident` row
to `~/.local/share/ubuntu-doctor/incidents.db`. Future runs retrieve
similar past incidents by Jaccard similarity on the fingerprint and feed
them to the LLM as few-shot examples.

The feedback store is **local only**. There is no upload.

---

## How it works

```
CLI → Orchestrator → Collectors (parallel, async)
                             ↓
      Snapshot (typed timeline + facts + degradation reports)
                             ↓
       Analyzers (parallel, rule-based) → [Hypothesis, ...]
                             ↓
        Ranker (symptom keyword boost for `ubuntu-doctor why`)
                             ↓
     RAG retrieval (changelogs, AppArmor profiles, apport)
    + similar past incidents from the local feedback store
                             ↓
           LLM (single call; structured JSON in/out)
                             ↓
        Renderer (text or JSON) + last-run cache write
```

### Collectors

Each collector reads one data source and returns typed `TimelineEvent`
objects, structured `facts`, and an optional `DegradationReport` if data
was unavailable (permission denied, missing tool, etc.). All collectors
run in parallel.

| Collector | Source | What it contributes |
|---|---|---|
| `dpkg_history` | `/var/log/dpkg.log` | Package install / upgrade / remove / purge events |
| `apt_log` | `/var/log/apt/history.log`, `apt-mark showhold`, `dpkg --audit` | apt transaction history; held & broken package facts |
| `snap_changes` | `snap changes`, `snap list`, `snap connections` | Snap refresh / install / remove events; current snap inventory |
| `systemd_failed` | `systemctl --failed` | Currently-failed units with `Result` and `LoadState` |
| `dmesg` | `journalctl --dmesg` | OOM kills, kernel taints, firmware load failures, ATA/NVMe/USB/PCIe AER errors, CPU lockups |
| `journald` | `journalctl --grep apparmor` | AppArmor denial events with parsed audit fields |
| `apparmor_audit` | `/var/log/audit/audit.log` | Deeper AppArmor history on servers running auditd |
| `hardware` | `lspci -nn`, `lsusb`, `ip link`, `dmidecode` | Hardware inventory for `firmware_mismatch` |
| `cache_state` | `/var/cache/apt/`, `/var/lib/dpkg/lock*`, `/var/lib/apt/lists/`, `/var/crash/` | Interrupted downloads, stale locks, list freshness |
| `diskspace` | `df`, `df -i` | Per-filesystem block and inode usage |

### Analyzers

Analyzers consume the merged `Snapshot` and emit `Hypothesis` objects with
a title, confidence score, rationale, evidence pointers, suggested fix
commands (never executed), read-only investigation steps, and risks.

| Analyzer | What it detects |
|---|---|
| `postupgrade_regression` | Package upgrades correlated with service failures via temporal proximity and package-to-unit name heuristics |
| `systemd_health` | Failed units classified by systemd's `Result` (oom-kill / core-dump / timeout / signal / masked / not-found / bad-setting), plus subsystem cluster detection when several related units fail together |
| `apparmor_denials` | AppArmor denials grouped by profile, confidence-boosted when an apparmor-related package or snapd was upgraded in the same window |
| `held_packages` | Held (`apt-mark showhold`) and broken (`dpkg --audit`) packages, surfaced as distinct hypotheses with distinct fixes |
| `firmware_mismatch` | dmesg firmware / hardware errors correlated with recent `linux-firmware` or `linux-image-*` upgrades; carries the affected PCI/USB IDs |
| `oom_attribution` | OOM kills grouped by killed process; confidence scales with repetition and service-restart correlation |
| `snap_refresh_breakage` | Snap refresh / install paired with AppArmor denials on the same snap's profile inside 24h |
| `irq_driver_regression` | NIC link / IRQ errors correlated with recent `irqbalance`, kernel, or NIC-driver-named package upgrades |
| `cache_health` | Interrupted apt downloads, stale dpkg locks, `/boot` filling up with old kernels, near-full critical mounts, inode pressure, stale apt list metadata |

### LLM call

`ubuntu-doctor` makes **one structured JSON call** per run. The prompt contains
the deterministic hypotheses, evidence timeline, retrieved RAG snippets,
similar past incidents from the feedback store, and the optional symptom.
The model returns a plain-English summary, re-ranked hypotheses with
confidence scores, suggested fix commands, investigation steps, risks,
and a "what I did not check" note.

The LLM has **no tool access**. It receives read-only context. Any
commands it suggests are rendered as copy-pasteable text; `ubuntu-doctor`
never executes them.

JSON parsing is lenient: the client strips markdown fences, extracts the
outermost `{...}` block, and validates required fields. Any failure mode
(connection refused, timeout, non-200, malformed JSON, hallucinated
hypothesis IDs) is surfaced as a degradation message and the tool falls
back to deterministic output.

### RAG retrieval

Retrieval is **event-driven** and scoped to the incident window — there
is no generic man-page / `/usr/share/doc` index.

- **Package changelogs** — `/usr/share/doc/<pkg>/changelog.Debian.gz`,
  extracted between the previous and current versions for each
  `PACKAGE_UPGRADE` in evidence.
- **NEWS files** — `/usr/share/doc/<pkg>/NEWS.Debian.gz` for major-version
  bumps, where present.
- **AppArmor profile bodies** — current profile under `/etc/apparmor.d/`
  or `/var/lib/snapd/apparmor/profiles/` for each denial.
- **apport crash reports** — `/var/crash/*.crash` matching failed-unit
  executables.

Snippets are de-duplicated by `(kind, source)`, truncated, and fed to the
LLM alongside the hypotheses. Disable retrieval with `--no-rag`.

### Local incident memory

`ubuntu-doctor feedback` writes one `Incident` per recorded outcome to
`~/.local/share/ubuntu-doctor/incidents.db` (SQLite, WAL mode). Each row
stores the fingerprint, chosen hypothesis ids, suggested vs. applied
commands, observed effect, outcome (`fixed` / `partially-fixed` /
`not-fixed` / `made-it-worse` / `unknown`), and free-text notes.

On the next `ubuntu-doctor` run, the orchestrator computes a fingerprint over
the current top hypotheses, finds similar past incidents by Jaccard
similarity, and includes them in the LLM prompt as few-shot examples.
Disable with `--no-history`.

### Privilege and degradation

`ubuntu-doctor` runs without root by default. Collectors that need elevated
access emit a `DegradationReport` with the exact `sudo` command that
would unlock more data — nothing is silently omitted. A "What I couldn't
see" section in the output lists every degradation with its unlock
command.

```
What I couldn't see:
  - apparmor_audit: cannot read /var/log/audit/audit.log
      to unlock: sudo cat /var/log/audit/audit.log >/dev/null
```

---

## Output formats

### Text (default)

Human-readable terminal output: LLM summary at the top, top hypotheses
with rationale / fix commands / investigation steps / risks, retrieved
RAG snippets that were used, similar past incidents (if any), and a
"What I couldn't see" tail. When `--no-ai` is used or the LLM is
unreachable, only the deterministic section is shown.

### JSON (`--json`)

Machine-readable. The payload includes `snapshot`, `hypotheses`,
`retrieved` (RAG), `past_incidents`, and (when available) `llm`. Useful
for piping into other tools or dashboards. When `--json` is in use, the
last-run cache is **not** written — the consumer is assumed to be a
pipeline, not an interactive user.

---

## Development

```bash
# install in editable mode with dev deps
pip install -e '.[dev]'

# run the CLI
ubuntu-doctor --no-ai
ubuntu-doctor --no-ai --json
ubuntu-doctor --no-ai --since 7d
ubuntu-doctor why "audio gone" --no-ai

# run tests
pytest -q
```

### Project layout

```
src/ubuntu_doctor/
├── cli.py                     # argparse, subcommands, analyzer selection
├── orchestrator.py            # async collector + analyzer fan-out
├── snapshot.py                # TimelineEvent, Snapshot, Hypothesis, DegradationReport
├── ranker.py                  # symptom keyword re-ranker
├── collectors/<source>/       # one folder per data source
│   └── plugin.py              # exposes COLLECTOR
├── analyzers/<rule>/          # one folder per correlation rule
│   └── plugin.py              # exposes ANALYZER
├── llm/                       # OpenAI-compatible client + prompts
├── rag/                       # event-scoped retrieval (changelogs, profiles, apport)
├── feedback/                  # incident store, last-run cache, recorder
└── ui/                        # text and JSON renderers
```

Each collector and analyzer is a self-contained plugin. New ones can be
added without touching any existing code — see `collectors/base.py` and
`analyzers/base.py` for the ABCs, then register the new id in
`COLLECTORS` / `ANALYZER_REGISTRY` in `cli.py`.

### Releasing to PyPI

```bash
# install build + twine
pip install -e '.[publish]'

# bump version in pyproject.toml, then:
rm -rf dist build
python -m build           # produces sdist + wheel in dist/
twine check dist/*        # validate metadata renders on PyPI

# dry-run on TestPyPI first
twine upload --repository testpypi dist/*
pip install -i https://test.pypi.org/simple/ ubuntu-doctor

# then the real thing
twine upload dist/*
```

Tag the release in git after a successful upload: `git tag v0.1 && git push --tags`.

---

## Design decisions

**Rules first, LLM explains.** A deterministic correlator produces the
candidate set. The LLM re-ranks and explains in plain English; it does
not invent hypotheses. `ubuntu-doctor --no-ai` must remain useful on its own.

**One LLM call per run.** The local Inference Snap serialises requests;
N agents = N × latency with no accuracy gain. Per-analyzer fan-out is
explicitly not the shape.

**Event-driven RAG.** No generic man-page index. Retrieval is scoped to
packages, profiles, and crash reports actually referenced by current
evidence.

**Privacy by default.** All data stays on the machine. The model runs in
the local Inference Snap. There is no telemetry, no upload, no cloud call.
The feedback store is local and single-user.

**Read-only.** No collector or analyzer writes to the system. The LLM
has no tool access. There is no `--apply` mode in v1; if it lands later,
it will re-prompt per command with the diff and effect.

---

## Status

Pre-alpha — v1 vertical slice landed.

Implemented:
- 10 collectors: `dpkg_history`, `apt_log`, `snap_changes`,
  `systemd_failed`, `dmesg`, `journald`, `apparmor_audit`, `hardware`,
  `cache_state`, `diskspace`
- 9 analyzers: `postupgrade_regression`, `systemd_health`,
  `apparmor_denials`, `held_packages`, `firmware_mismatch`,
  `oom_attribution`, `snap_refresh_breakage`, `irq_driver_regression`,
  `cache_health`
- Event-scoped RAG over changelogs, NEWS files, AppArmor profiles, and
  apport reports
- Local incident memory (SQLite + Jaccard similarity) with the
  `ubuntu-doctor feedback` flow and a last-run cache
- LLM client against the Canonical Inference Snap with lenient JSON
  parsing and graceful degradation
- Symptom-keyword ranker for `ubuntu-doctor why <symptom>`
- CLI: `ubuntu-doctor`, `ubuntu-doctor why <symptom>`, `ubuntu-doctor feedback`, with
  `--no-ai`, `--no-rag`, `--no-history`, `--analyzers`,
  `--skip-analyzers`, `--json`, `--since`, `--model`, `--base-url`,
  `--llm-timeout`

Not yet implemented:
- `ubuntu-doctor explain <hypothesis-id>` subcommand
- `--deep` mode for per-analyzer follow-up LLM calls
- `ubuntu-doctor feedback --revisit` for re-asking about unresolved cases
- Embedding-based similarity in the feedback store (current is Jaccard)

See [docs/plan.md](docs/plan.md) for the full design and backlog.

---

## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE) for the
full text and authorship.
