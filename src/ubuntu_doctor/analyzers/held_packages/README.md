# held_packages analyzer

Reads `apt_log` facts (`held_packages`, `broken_packages`) and emits
two distinct hypothesis classes because their fixes differ:

- **Broken packages** — confidence 0.8. Surfaces `dpkg --audit`
  output. Concrete fix: `sudo dpkg --configure -a` + `sudo apt install -f`.
- **Held packages** — confidence 0.55, bumped to 0.7 if combined with
  broken packages (the hold is probably blocking dependency resolution).
  **No fix commands** — holds are typically intentional and unholding
  blindly can re-introduce whatever the user pinned around. The
  rationale calls this out.
