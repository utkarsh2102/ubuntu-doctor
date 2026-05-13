# ubuntu-doctor — demo

A repeatable, ~3-4 minute asciinema demo for an engineering audience. The
seed script plants three safe, reversible faults on your laptop. You record
`doctor` finding them and the local LLM explaining them. The teardown
script undoes everything.

## What gets seeded

| Fault | Analyzer it triggers |
|---|---|
| Failing oneshot systemd unit (`doctor-demo-fail.service`) | `systemd_health` |
| One held package (the first of `cowsay`, `sl`, `htop`, `tree`, `neofetch`, `fortune` that's installed) | `held_packages` |
| A zero-byte file in `/var/cache/apt/archives/partial/` | `cache_health` |

Three different correlators fire, the LLM synthesises a single narrative.

If you also have natural package upgrades in your `dpkg.log` from the last
14 days (very likely), `postupgrade_regression` will probably fire too,
pairing the failing unit with whatever was upgraded.

## Pre-flight

```bash
# Inference Snap up?
curl -fsS http://localhost:8336/v1/models | head -c 200

# editable install ready?
source .venv/bin/activate
doctor --help | head -3

# asciinema installed?
sudo apt install asciinema   # or: sudo snap install asciinema
```

If the Inference Snap isn't reachable, `doctor` will fall back to
`--no-ai` automatically — but for this demo you want the LLM, so make
sure `sudo snap install gemma4` has finished and the endpoint responds.

## Seed

```bash
sudo ./demo/seed.sh
```

Verify the faults landed:

```bash
systemctl --failed | grep doctor-demo-fail
apt-mark showhold
ls /var/cache/apt/archives/partial/
```

## Record

```bash
asciinema rec doctor-demo.cast \
  --idle-time-limit 2 \
  --title "ubuntu-doctor — local diagnosis for Ubuntu"
```

`--idle-time-limit 2` collapses LLM-thinking pauses to 2 seconds in
playback, which keeps the recording watchable without losing the
real-time feel.

When the recording is open, run the scenes below. Hit `^D` (or
`exit`) when you're done to stop recording.

### Scene 1 — passive diagnosis (~90s)

> "Three things broke on this machine in the last week. None of them
> obviously related. Watch."

```bash
doctor
```

What to point at while it runs:
- the **window line** at the top — collectors ran in parallel under
  a few seconds
- the **LLM summary** — one paragraph synthesising heterogeneous
  evidence (a failed unit + a held package + a partial download)
- the **fix commands** — note they're rendered as text, never run
- the **"What I couldn't see"** section if any — degradation reports
  with the exact `sudo` to unlock more

### Scene 2 — symptom-directed diagnosis (~60s)

> "Now the same data, but I'm asking a specific question."

```bash
doctor why "package upgrades aren't going through"
```

The symptom boosts hypotheses mentioning `apt`, `dpkg`, `held` —
`held_packages` and `cache_health` should rise above `systemd_health`
in the ranking. Engineers see the rule-based ranker doing real work
*before* the LLM weighs in.

### Scene 3 — composability (~30s)

> "It's a Unix tool. JSON out, pipe wherever."

```bash
doctor --no-ai --json | jq '.hypotheses[] | {id, analyzer, confidence, title}'
```

Skipping the LLM here keeps the output deterministic for the
recording. Shows the deterministic backbone is useful on its own.

### Scene 4 — closing the loop (~30s)

> "When you actually fix it, doctor learns."

```bash
doctor feedback
```

Walk through the first prompt (which hypothesis was it?), then `^C`
out — completing the flow writes to `~/.local/share/ubuntu-doctor/
incidents.db` which is fine, but the demo doesn't need a full
write to make the point.

End the recording (`^D`).

## Convert / share

```bash
# Play it back locally
asciinema play doctor-demo.cast

# Speed up if it feels slow
asciinema play doctor-demo.cast --speed 1.5

# Upload (asciinema.org, public link)
asciinema upload doctor-demo.cast

# Convert to GIF for slides (needs agg from charmbracelet/agg)
agg doctor-demo.cast doctor-demo.gif --speed 1.4
```

## Teardown

```bash
sudo ./demo/teardown.sh
```

Removes the unit, unholds the package, deletes the partial-download
file. Idempotent — safe to run twice.

## Talking points (Q&A bait)

- **"Why not just send logs to GPT-4?"** Local-only is the entire pitch.
  No data leaves the machine. The Inference Snap runs on the same box.
- **"How does it scale to other distros?"** It doesn't, deliberately.
  Ubuntu-specific evidence (dpkg/apt, snap, AppArmor, journald) is the
  whole point. A RHEL/Arch port would be a different tool.
- **"Why rules-first if you have an LLM?"** The LLM doesn't invent
  hypotheses, only ranks and explains them. `doctor --no-ai` must stay
  useful — that's the regression bedrock for the deterministic layer.
- **"What about agentic fan-out per analyzer?"** Local inference is
  serialised; N agents = N × latency with no accuracy gain. One call
  per run by design.
- **"Read-only forever?"** v1 yes. A future `--apply` mode would
  re-prompt per command with the diff/effect summary — but the LLM
  itself never gets tool access.
