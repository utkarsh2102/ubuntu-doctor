# oom_attribution analyzer

Groups `OOM_KILL` events (from the dmesg collector) by killed
process. Surfaces repetition: a single OOM kill is mostly noise but
the same process being killed N times in window is a real signal.

| Repetition                                      | Confidence |
|------------------------------------------------|-----------:|
| 1 kill, no nearby service failure              | 0.40 |
| 1 kill + a `SERVICE_FAILED` within ±1h         | 0.55 |
| 2–4 kills of the same process                  | 0.65 |
| 5+ kills of the same process                   | 0.80 |

**Why no `fix_commands`.** OOM-kill is a *symptom*, not a cause. The
killed process is often the leaker, but not always — sometimes it's
just the largest victim of someone else's leak. Proposing a fix
without that context risks shifting the failure or masking it. The
rationale calls this out explicitly. Investigation steps point at
`journalctl`, `dmesg`, and `ps --sort=-rss` to identify the actual
memory consumer.
