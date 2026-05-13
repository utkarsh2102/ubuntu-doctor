# Basic Description

You are a senior open source software engineer with deep expertise in Linux internals,
Ubuntu/Debian packaging, systemd, APT, and Python. You care about clean interfaces,
composable tools, and writing software that works correctly in the real world — not
just demos. Your job is to think sharper and make realistic plans AND NOT AGREE WITH US! Think through this problem deeply before you answer. Analyse the core assumptions of my prompt and if they are flawed, address that first!

We are building **ubuntu-doctor**: a CLI tool that diagnoses Ubuntu system problems
and explains them in plain language. Think of it as `git blame` for your broken system.

Such kind of problems could be merely warnings that might later surface as unexpected failures on seemingly unrelated components of the system. Its not just about the system's logs, its also about configurations and the state of potential caches (/var/cache), like the apt cache, to make sure everything runs smoothly. This tool shall be called 'ubuntu-doctor'. The tool itself will be a traditionally piece of software that runs locally and controls which files are going to accessed for analysis. However, for the analysis itself it should use an LLM to find issues and propose solutions. those solutions can steered by the LLM and implemented on the system AFTER USER CONFIRMATION. To meet the requirements with privacy constraints, everything should be running locally, also the LLM. Currently we have Ubuntu Interference Snaps in mind, specifically the gemma4:e4b model, as it is good balance for good reasoning on less powerful edge devices.

## What it is

`ubuntu-doctor` gathers system state (recent package changes, failed services, kernel
events, AppArmor denials, snap changes, hardware errors) and builds a timeline of
what changed on the system. It sends that timeline to an AI model and gets back a
plain-English diagnosis with a concrete suggested fix.

It has two modes:

- `ubuntu-doctor` — passive diagnosis. "What is wrong with my system right now?"
- `ubuntu-doctor why <symptom>` — active diagnosis. "Why did my audio stop working?"

The AI part is not decorative. The correlation between heterogeneous log sources is
genuinely hard to do with static rules. The LLM earns its place here.

## What it is not

- Not a monitoring tool (use Prometheus/Grafana for that)
- Not a package manager wrapper
- Not a chatbot

## Design principles

1. **Collect first, think second.** Collectors are pure data gatherers. The AI layer
   sees a structured snapshot, not raw logs.
2. **Never require root for basic operation.** Some collectors need sudo for deeper
   access; the tool degrades gracefully without it.
3. **Fast by default.** The default run should finish in under 3 seconds before the
   API call. No collector should block the others.
4. **Composable.** `ubuntu-doctor --json` outputs machine-readable data. Other tools can
   consume it.
5. **Honest about uncertainty.** If the AI isn't confident, it says so. No
   hallucinated fixes.

## Why this exists

When Ubuntu breaks, the information you need to fix it is scattered across a dozen
places: `journalctl`, `dmesg`, `/var/log/apt/history.log`, `snap changes`, `dpkg -l`.
The problem isn't that the logs don't exist — it's that correlating them is tedious,
and the symptom you see (audio gone, Bluetooth dead, network slow) is rarely in the
same place as the cause (kernel/driver mismatch, held package, missing firmware).

`ubuntu-doctor` does that correlation for you.

---

## Real situations this would have helped

**1. The post-upgrade audio silence (Ask Ubuntu, 50k views)**

A user runs `apt upgrade`, reboots, and has no sound. The actual cause — nvidia driver
held back due to a dependency conflict, which destabilised PulseAudio's device
enumeration — is not visible anywhere obvious. The top-voted answer involves three
different commands across two Stack Overflow threads and still doesn't explain *why*
it happened.

`ubuntu-doctor` sees: kernel upgraded, nvidia held, pulseaudio crashed repeatedly. Connects
them. Says what to do.

---

**2. The mystery network drop on a cloud VM**

A developer's EC2 Ubuntu instance starts dropping packets intermittently after a
routine `unattended-upgrade` run. The cause: `irqbalance` was upgraded and its
config changed the IRQ affinity for the NIC. Nothing in the standard logs makes
this obvious. `dmesg` shows errors but not the cause.

`ubuntu-doctor` sees: `irqbalance` upgraded, network IRQ errors in dmesg starting at the
same time. Surfaces the correlation. Points to the config diff.

---

**3. "My snap app just stopped working"**

A user's Spotify snap stopped launching silently after a system update. The cause:
an AppArmor policy update that now denies access to `~/.config/pulse`. The app
produces no visible error. There's no notification. The user just double-clicks and
nothing happens.

`ubuntu-doctor` sees: AppArmor denial for the Spotify snap, timestamp correlates with
snapd refresh. Explains the denial in plain English. Suggests the right `snap
connect` command.

---

**4. The sysadmin's 2am server crisis**

A production Ubuntu server starts throwing OOM errors. Services are restarting
randomly. The sysadmin is tired and needs to know where to look first.

`ubuntu-doctor --deep` gathers memory pressure events from `dmesg`, correlates with which
services were restarting, checks if any recent package installs changed memory
footprint, and ranks the candidates by likelihood.

---

**5. The new laptop Wi-Fi regression**

A user installs Ubuntu on a new laptop and Wi-Fi works. After the first
`apt upgrade`, it stops. The reason: the linux-firmware package was upgraded and
the new firmware for their specific Realtek card has a regression.

`ubuntu-doctor` sees: linux-firmware upgraded, Wi-Fi errors in dmesg starting at next
boot, hardware ID of the card. Explains what happened and links to the relevant
Ubuntu bug.

# Help us create a plan:
- suggest a suitable programming language to implement ubuntu-doctor
- suggest a high-level architecture for the analysis, with extensible plugin support, reflected by separate subfolders for each plugin in the project's file structure
- suggest feasible analyses (which types of issues to analyze), and for each of them lay out
    - how to detect that issue? 
    - what are possible measures to solve it?
- do you think that gemma4:e4b is a suitable target model or would you suggest a different one?
- also suggest us how an interference snap can be connected to ubuntu-doctor (for your knowledge: interference snaps can be accessed via the OpenAI API directly via the API URL without any tokens)
    - can that be done as a simple skill?
    - do we need an MCP for that?
    - are there other ways to integrate a local LLM to ubuntu-doctor?
    - BEST CASE SCENARIO: ubuntu-doctor acts like a parent agent, spawning a separate child agent for each supported plugin, and all of those agents use the same local LLM.
- we like to have a RAG, incorporating additional information from the local system, please elaborate on possible sources of documentation, hardware information, configuration etc. we could think of man pages and the local package documentation (/usr/share/doc). Do you have an more ideas?
- we want to have local caching, because reading tons of data that might not have significantly changed does not make sense. It would be nice to just figure out the delta since the last runs.
- incorporate a way for the user to give some feedback (feedback loop) to improve the llm's knowledge base
- generally speaking, do you think there is a better way to do all of this?

# IMPORTANT Guardrails:
- DO NOT GENERATE ANY CODE YET, we are in the planning phase, just discuss with us to refine our ideas, so we can craft a detailed plan
- ubuntu-doctor's LLM should ONLY READ the system for analyzing, do NOT under any circumstanced MAKE ANY CHANGES TO THE SYSTEM without asking the user first!
