# apparmor_denials analyzer

Groups `APPARMOR_DENIED` events (produced by the `journald` collector)
by profile and emits one hypothesis per profile. Confidence is boosted
when an AppArmor-related package or `snapd` was upgraded within the
correlation window (48h) of the latest denial for that profile.

**Confidence model:**

| Situation                                                                 | Confidence |
|---------------------------------------------------------------------------|-----------:|
| Denials only (no related upgrade nearby)                                  | 0.50       |
| Denials + AppArmor/`apparmor-profiles`/`libapparmor1` upgrade in window   | 0.65       |
| Snap-profile denials + `snapd` upgrade in window                          | 0.70       |

**Profile classification & remediation:**

- **Snap profiles** (`snap.<name>.<command>`): rationale notes that the
  most common cause is a missing snap interface connection. Suggested
  commands surface `snap connections <name>` and the relevant
  `snap interfaces` query.
- **System profiles** (e.g. `usr.bin.firefox`): suggested commands point
  at `/etc/apparmor.d/` and `aa-status --profiled` to find the on-disk
  profile.

**Investigation commands** are read-only. The analyzer NEVER suggests
`aa-complain` or `aa-disable` without a loud risk warning: silencing
AppArmor is not a fix, it removes the protection that fired. For snap
profiles, it also warns that `snap connect <snap>:<iface>` grants
system-wide capabilities and the user should read
`snap interface <name>` first.

**Known limitation (v1):** profile → package mapping is heuristic
(snap-name → snapd, profile-name → substring match). A future iteration
could call `dpkg -S /etc/apparmor.d/<profile>` to resolve ownership
precisely. The current heuristic is enough for the motivating cases
(snap denials after snapd refresh, system profile breakage after
apparmor-profiles upgrade) and avoids per-denial subprocess fan-out.
