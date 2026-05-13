# ubuntu-doctor

A CLI that diagnoses Ubuntu system problems and explains them in plain
language. It builds a timeline of recent system changes (package upgrades,
service failures, kernel events, AppArmor denials, snap refreshes, hardware
errors) and correlates them deterministically. A **local** LLM running in
the Ubuntu Inference Snap then explains the top hypotheses.

```bash
doctor                  # what is wrong with my system right now?
doctor why <symptom>    # why did <my audio> stop working?
doctor --no-ai          # deterministic only, no LLM
doctor --json           # machine-readable
```

`ubuntu-doctor` is read-only. Suggested fixes are rendered as copy-pasteable
commands; nothing is executed without you.

See [docs/plan.md](docs/plan.md) for the design and [CLAUDE.md](CLAUDE.md)
for a one-screen overview.

## Install (development)

Requires Python 3.12+.

```bash
git clone <this-repo> ubuntu-doctor
cd ubuntu-doctor

python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The editable install registers a `doctor` console script inside the venv:

```bash
.venv/bin/doctor --since 14d
.venv/bin/doctor --json --since 7d
.venv/bin/pytest -q
```

To call `doctor` without the `.venv/bin/` prefix, either activate the
venv (`source .venv/bin/activate`) or add `.venv/bin` to your `PATH`.

## Status

Pre-alpha. v1 vertical slice in progress.
