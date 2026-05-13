# ubuntu-doctor — Implementation Plan (v1)

## Context

When Ubuntu breaks, the evidence is scattered (`journalctl`, `dmesg`,
`/var/log/apt/history.log`, `snap changes`, `dpkg -l`, AppArmor audit) and the
symptom (no audio, dropped packets, silent snap failure) is rarely co-located
with the cause (held nvidia, irqbalance config change, AppArmor profile bump,
firmware regression). `ubuntu-doctor` correlates those sources into a timeline,
ranks likely causes deterministically, and uses a **local** LLM to explain the
top hypotheses in plain English. Privacy stays intact because nothing leaves
the machine; the model runs in the Ubuntu **Inference Snap** (note: not
"Interference") and is reached over an OpenAI-compatible HTTP endpoint at
`http://localhost:8336/v1`.

The tool is **read-only by default**. Any suggested fix is rendered as a
copy-pastable command. Applying a fix requires explicit user action (and, in a
future `--apply` mode, per-command confirmation). The LLM never executes
anything, never writes files, never invokes package managers.

This plan reflects four decisions confirmed with the user:
- **Rules-first, LLM-explains.** A deterministic correlator produces ranked
  hypotheses; the LLM re-ranks and explains. `doctor --no-ai` still works.
- **Plugins are code modules, one LLM call by default**; `--deep` allows
  per-analyzer follow-up LLM calls for the rare ambiguous case.
- **Local incident memory** (SQLite + vector index), RAG'd on similar inputs.
- **Unprivileged by default**, with each collector reporting degradation and
  the summary telling the user exactly which sudo command unlocks more data.

## Corrections to upstream assumptions

- **Model: `gemma:e4b` is the agreed default** (confirmed live against
  the user's Inference Snap; the endpoint also accepts the canonical
  id `gemma4-e4b-q4-k-m`, which is the same loaded model). I'd still
  benchmark against a larger sibling on representative snapshots: 4B-
  class models can struggle with multi-source correlation when the
  structured input is long. Model name is a config knob (`--model`,
  env var, config file) so swaps are cheap.
- **Multi-agent (one LLM agent per plugin) is the wrong shape.** Local
  inference is serialised; N agents = N × latency, no accuracy gain. One
  call per `doctor` run with all collector output structured into the prompt
  is faster and more reproducible. `--deep` is the escape hatch.
- **RAG over `man` and `/usr/share/doc` is mostly noise.** Useful sources are
  scoped to packages/events in the incident window: changelogs, NEWS,
  AppArmor profile diffs, apport reports, taint flags, incident memory.
- **"Feedback to improve the LLM" = local RAG memory, not fine-tuning.**
  Local frozen weights cannot be updated by end users; we persist
  user-confirmed (problem → fix → outcome) tuples and retrieve them as
  few-shot examples.

## Language & distribution

- **Python 3.12**. Most data sources are subprocess wrappers (`apt`,
  `journalctl`, `snap`, `dpkg`, `lspci`); CPython is fast enough; the LLM
  call dominates. Ubuntu ships Python in base. Use `python3-apt` and
  `systemd.journal` Python bindings where available.
- **Concurrency:** `asyncio` + `asyncio.subprocess` for the collector
  fan-out. Hard 3s wall-clock budget per `doctor` run for collection.
- **Distribution:** pip package first; a confined `snap` later for users
  who want sandboxing. Avoid premature snap packaging — confinement makes
  reading host data harder, which is the entire point of the tool.

## Project layout

```
ubuntu-doctor/
├── pyproject.toml
├── README.md
├── src/ubuntu_doctor/
│   ├── __main__.py
│   ├── cli.py                       # argparse, subcommands: doctor / why / explain
│   ├── orchestrator.py              # collectors → analyzers → LLM
│   ├── snapshot.py                  # TimelineEvent, Snapshot, Hypothesis dataclasses
│   ├── cache/
│   │   ├── cursors.py               # journald cursor, dpkg history offset, ...
│   │   ├── snapshots.py             # structured snapshot cache (by cursor hash)
│   │   └── diagnoses.py             # LLM-output cache keyed by (snapshot hash + model + prompt rev)
│   ├── llm/
│   │   ├── client.py                # OpenAI-compatible HTTP, streaming, structured-output schema
│   │   ├── prompts.py               # versioned prompt templates
│   │   └── retrieval.py             # changelog / NEWS / incident-memory retrieval
│   ├── collectors/                  # one folder per data source (plugin)
│   │   ├── base.py                  # Collector ABC: collect(), degradation, time budget
│   │   ├── journald/
│   │   ├── dpkg_history/
│   │   ├── apt_log/
│   │   ├── snap_changes/
│   │   ├── dmesg/
│   │   ├── apparmor_audit/
│   │   ├── systemd_failed/
│   │   ├── hardware/                # lspci/lsusb/dmidecode/ip link
│   │   ├── cache_state/             # /var/cache/apt, dpkg locks, /var/crash
│   │   └── diskspace/
│   ├── analyzers/                   # one folder per correlation rule (plugin)
│   │   ├── base.py                  # Analyzer ABC: analyze(snapshot) -> list[Hypothesis]
│   │   ├── postupgrade_regression/
│   │   ├── held_packages/
│   │   ├── apparmor_denials/
│   │   ├── firmware_mismatch/
│   │   ├── oom_attribution/
│   │   ├── snap_refresh_breakage/
│   │   ├── irq_driver_regression/
│   │   └── cache_health/
│   ├── feedback/
│   │   ├── store.py                 # SQLite + sqlite-vec (or chromadb) for incident memory
│   │   └── thumbs.py                # CLI prompt for thumbs-up/down, fix-applied?, fix-worked?
│   └── ui/
│       ├── text.py                  # default human output (rich/colour optional)
│       └── jsonout.py               # --json
└── tests/
    ├── fixtures/                    # captured snapshots from real incidents
    ├── collectors/
    ├── analyzers/
    └── e2e/                         # `doctor --no-ai` against fixture snapshots
```

Each plugin folder contains a `plugin.py` with the entry-point class, a
narrow `README.md` describing its scope and degradation behaviour, and a
`tests/` subfolder. Plugins are discovered via `importlib.metadata` entry
points so third parties can ship their own.

## Data flow

1. **CLI** parses subcommand (`doctor`, `doctor why <symptom>`,
   `doctor explain <event-id>`, `doctor feedback`).
2. **Orchestrator** loads collectors in parallel under a 3s budget. Each
   collector returns a partial `Snapshot` slice plus a `DegradationReport`.
3. **Orchestrator** merges slices into one `Snapshot` (a typed,
   chronologically sorted event timeline + system-state facts).
4. **Analyzers** run in parallel against the snapshot. Each emits zero or
   more `Hypothesis` objects with: title, evidence pointers, confidence,
   suggested remediation (text + command, never executed).
5. **Ranker** combines analyzer confidences with prior outcomes from local
   incident memory (boost hypotheses whose past fixes were marked "worked";
   demote ones marked "didn't work").
6. **LLM call (single, structured output)** — receives top-K hypotheses,
   relevant changelog/NEWS excerpts, and matching past-incident examples;
   returns a re-ranked, plain-English diagnosis with confidence and "what
   I checked but didn't see".
7. **Renderer** emits text or JSON. Commands are formatted for copy-paste,
   never executed.

`--no-ai` short-circuits step 6, printing the deterministic hypotheses.
`--deep` allows specific analyzers (configurable) to make their own
follow-up LLM call when the top-level confidence is below threshold.

## Initial analyzers (detection + remediation)

Each one is a v1 deliverable. Detection is rule-based; the LLM only
explains and ranks.

**1. Post-upgrade service regression**
- *Detect:* `dpkg.log` shows package P upgraded at T; `systemctl --failed`
  or journal grep shows unit U failed ≥ N times in [T, T+Δ]. Cross-match
  P against `dpkg -L | grep systemd` and against systemd `Wants=` /
  `Requires=` graph.
- *Remediation:* propose `apt install P=<previous-version>` with
  `apt-mark hold P`, link to Launchpad search URL.

**2. Held / broken packages**
- *Detect:* `apt-mark showhold`, `dpkg --audit`, `apt-get check` exit
  code, error patterns in `/var/log/apt/term.log`.
- *Remediation:* explain *why* the hold or break exists (parse the term
  log), suggest `apt install -f` or targeted resolution.

**3. AppArmor denials correlated with snap/policy refresh**
- *Detect:* parse `audit.log` (or `journalctl -k _TRANSPORT=audit`) for
  `apparmor="DENIED"`; correlate with `snap changes` and
  `/var/log/dpkg.log` entries that touch `apparmor`-related packages.
- *Remediation:* surface the specific `snap connect ...` command, or
  point at the profile path under `/var/lib/snapd/apparmor/profiles/`.

**4. Kernel / firmware mismatch**
- *Detect:* dmesg "firmware: failed to load" or "regulatory.db missing"
  or "firmware bug", plus `linux-firmware` recently upgraded, plus
  `uname -r` vs `dpkg -l 'linux-image-*'` mismatch. Capture hardware ID
  via `lspci -nn` for the affected device.
- *Remediation:* propose firmware/kernel downgrade with exact version,
  emit a Launchpad search URL keyed by hardware ID.

**5. OOM attribution**
- *Detect:* dmesg "Out of memory: Killed process" lines, the killed
  process name and parent, services restarted after each kill (journald
  `_SYSTEMD_INVOCATION_ID` change), recent installs/upgrades for those
  processes' packages.
- *Remediation:* rank likely culprits by frequency and memory growth;
  suggest cgroup `MemoryHigh=`/`MemoryMax=` for the worst offender; do
  *not* claim to know it's a leak unless growth data supports it.

**6. Snap refresh breakage**
- *Detect:* `snap changes` shows recent refresh of snap S; AppArmor
  denials reference S; `~/snap/S/common/` or system journal contains
  recent crash entries for S.
- *Remediation:* `snap revert S` command, optional auto-refresh hold
  syntax, Launchpad search link.

**7. IRQ / driver regression (cloud-VM-style network drop)**
- *Detect:* dmesg NIC/IRQ errors timestamped within Δ of an `irqbalance`,
  kernel, or NIC-driver package upgrade. Inspect `/proc/interrupts`
  distribution before/after if cached.
- *Remediation:* roll back the offending package, surface the config diff
  from `/etc/default/irqbalance` if changed.

**8. Cache & state health (preventive)**
- *Detect:* `/var/cache/apt/archives/partial` non-empty, dpkg locks
  orphaned, `/var/lib/dpkg/info` checksum mismatches, `/boot` near full,
  `/var` near full.
- *Remediation:* `apt clean`, `dpkg --configure -a`, targeted lockfile
  removal with explicit warnings, kernel cleanup suggestions.

Future analyzers (out of v1): boot/initramfs failures, NetworkManager
plan diffs, journald rate-limit truncation, time-sync drift, GPU
driver/Xorg/Wayland session crashes.

## LLM integration

- **Transport:** OpenAI-compatible HTTP. Default base URL
  `http://localhost:8336/v1` (Inference Snap default), overridable via
  `--base-url`, env var, or config file. No auth token required for the
  local snap.
- **Structured output:** request JSON Schema-constrained responses
  (`response_format: json_schema`) so the renderer can rely on shape.
  Fields: `summary`, `ranked_hypotheses[]` (each with `id`, `title`,
  `why_i_think_this`, `confidence` 0-1, `commands[]`, `risks[]`), and
  `what_i_did_not_check`.
- **No MCP for the internal call.** A single process → single endpoint
  doesn't benefit from MCP. *Future, separate deliverable:* expose
  ubuntu-doctor's collectors via an MCP server so Claude Code / other
  agents can query system state. Out of v1.
- **No multi-agent inside ubuntu-doctor.** One call per run by default.
  `--deep` opens the door to per-analyzer follow-ups for low-confidence
  cases, gated by a `max_followup_calls` config.

## RAG: scoped, on-demand evidence retrieval

Retrieval is **event-driven**, not corpus-wide. For each top hypothesis,
fetch:

- **Package changelogs** — `/usr/share/doc/<pkg>/changelog.Debian.gz`
  for packages in the incident window. Extract entries between the
  previous and current versions.
- **NEWS files** — `/usr/share/doc/<pkg>/NEWS.Debian.gz` for major-version
  bumps; high signal for breaking changes.
- **AppArmor profile diffs** — current profile under
  `/etc/apparmor.d/` and `/var/lib/snapd/apparmor/profiles/`; compare
  against the previous version if cached.
- **apport reports** — `/var/crash/*.crash` (when readable), parsed for
  signal, ExecutablePath, ProcMaps.
- **Kernel taint flags & modules** — `/proc/sys/kernel/tainted`,
  `lsmod`, `modinfo` for modules referenced in dmesg.
- **Local incident memory** — past confirmed (problem, fix, outcome)
  tuples, retrieved by embedding similarity over a fingerprint of the
  current snapshot (top events + analyzer IDs).

Generic man-page / `/usr/share/doc` indexing is **explicitly excluded
from v1**. Add only if a concrete case shows it would have helped.

## Caching layers

Three layers with different invalidation rules:

1. **Cursors** (cheapest, append-only): journald cursor token, dpkg log
   offset, apt history.log offset, snap-changes max ID, dmesg
   timestamp. Stored in `~/.cache/ubuntu-doctor/cursors.json`.
2. **Structured snapshot fragments**: keyed by `(collector_id,
   cursor_from, cursor_to)`. SQLite. Lets us avoid re-parsing.
3. **LLM diagnoses**: keyed by `hash(snapshot) + model + prompt_rev`.
   Useful only within short windows but cheap to have.

Delta runs: each collector reports new events since the last cursor.
Analyzers always operate on a window (default: last 14 days) — they
don't need the full history, so we trim per run, not just per cache.

## Feedback loop (local incident memory)

When the user reads a diagnosis, the CLI offers:
```
[Y] this was the cause   [N] not the cause   [?] unsure   [s] skip
```
On any branch (including `N` and `?`) the user can record their own
narrative — this is the part the LLM most benefits from re-reading later.
The interactive prompt collects, optionally and editable in `$EDITOR`:

- **Which hypothesis (if any) they followed.** Multi-select; `none` is
  valid and useful when the real cause was something we missed.
- **Commands they actually ran.** Free text; pre-filled with the
  commands we suggested so the user only edits what they changed.
- **What happened after.** Free text describing the observed effect —
  "audio came back after reboot", "still no Wi-Fi", "different error
  now: …". This is the highest-signal field for future RAG.
- **Outcome flag.** `fixed` / `partially-fixed` / `not-fixed` / `made-it-worse`
  / `unknown`. Checkable later via `doctor feedback --revisit`.
- **Free notes.** Anything else — context the user thinks matters,
  links to bug reports, "I also rolled back package X".

Stored as SQLite rows in `~/.local/share/ubuntu-doctor/incidents.db`:
```
incident(id, ts, snapshot_hash, fingerprint_embedding,
         chosen_hypothesis_ids, suggested_commands, applied_commands,
         observed_effect, outcome, notes, revisited_at)
```
Retrieved at run time via vector similarity over `fingerprint_embedding`
(top events + analyzer IDs + hardware fingerprint). Top-K examples are
injected into the LLM prompt as few-shot. Outcomes feed back into the
ranker's prior.

Pure local. No upload. If we later want a community corpus, that's a
separate, opt-in feature with its own privacy review.

## CLI surface (v1)

```
doctor                        # passive diagnosis, last 14 days
doctor why <symptom>          # active, e.g. "audio gone", "wifi flaky"
doctor explain <hypothesis-id># expand evidence + commands for one hypothesis
doctor feedback               # record outcome of a past diagnosis
doctor feedback --revisit     # ask "did the fix work?" for unresolved cases
doctor --json                 # machine-readable
doctor --no-ai                # deterministic only, skip LLM
doctor --deep                 # allow per-analyzer follow-up LLM calls
doctor --since <when>         # override default window
doctor --model <name>         # override default gemma:e4b
doctor --base-url <url>       # override OpenAI-compatible endpoint
doctor doctor                 # report ubuntu-doctor's own health (collectors,
                              # snap reachability, cache size)
```

## Privilege & safety

- Default: unprivileged. Collectors that fail due to permissions emit a
  `DegradationReport` with the *exact* sudo command that would unlock
  them. The final summary lists these in a "What I couldn't see"
  section. No silent omission.
- The LLM is given **read-only context only**. It cannot tool-call into
  collectors, write files, or invoke commands. Suggested commands are
  rendered as text; the user copies them.
- Optional future `--apply` mode would re-prompt the user *per command*
  with the diff/effect summary; out of v1.

## Verification

- **Unit tests** per collector against captured fixtures
  (`tests/fixtures/journald-spotify-denial.txt`, etc.).
- **Analyzer tests** against synthetic snapshots covering the five
  motivating real-world incidents. Each must produce the correct top
  hypothesis with `--no-ai`. These are the regression bedrock.
- **End-to-end smoke test** on a clean Ubuntu LTS VM with deliberately
  broken state (held nvidia, denied snap, missing firmware) — verify
  `doctor` reports correctly with and without the Inference Snap.
- **LLM prompt regression**: capture (snapshot → diagnosis) golden
  pairs; CI re-runs them against pinned model+prompt version and flags
  drift. Acceptable diff threshold is loose (LLM nondeterminism) but
  catastrophic failures (wrong hypothesis ranked top, hallucinated
  commands) trip the test.
- **Manual UX pass**: run `doctor` on the developer's daily-driver
  machines; capture and review every diagnosis before v1 ships.

## v1 scope explicitly

In: collectors + analyzers listed above; one-shot LLM explainer; local
incident memory; cursor-based delta; `--json`, `--no-ai`, `--deep`,
`--why`, `--explain`, `--feedback`; unprivileged default with clear
degradation messages.

Out: applying fixes (`--apply`), MCP collector server, community
incident corpus, snap distribution, generic man-page RAG, multi-agent
orchestration, GUI.

## Critical files (paths to create)

- `pyproject.toml`
- `src/ubuntu_doctor/cli.py`
- `src/ubuntu_doctor/orchestrator.py`
- `src/ubuntu_doctor/snapshot.py`
- `src/ubuntu_doctor/collectors/base.py`
- `src/ubuntu_doctor/analyzers/base.py`
- `src/ubuntu_doctor/llm/client.py`
- `src/ubuntu_doctor/llm/prompts.py`
- `src/ubuntu_doctor/feedback/store.py`
- One folder under `collectors/` and `analyzers/` per item listed above,
  each with `plugin.py`, `README.md`, `tests/`.

## Open follow-ups (not blockers for v1)

- Benchmark `gemma:e4b` against a larger Inference Snap sibling on
  representative snapshots before the prompt is finalised — the
  structured-output budget may force a larger model on long timelines.
- Decide whether the `--deep` mode follow-up calls should be analyzer-
  initiated (an analyzer requests "I need more context on X") or
  explainer-initiated (the explainer asks for more after seeing top-K).
  Probably explainer-initiated, but worth a focused prototype.
- Decide vector store: `sqlite-vec` (single file, no extra service) vs
  bundling a tiny ONNX embedder. Default to `sqlite-vec` + a small
  on-disk embedder; revisit if recall is weak.
