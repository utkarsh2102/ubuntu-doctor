#!/usr/bin/env bash
# demo/seed.sh — plant safe, reversible faults so ubuntu-doctor has something
# to find during the demo. Three faults, three different analyzers:
#
#   - failing systemd unit          → systemd_health
#   - held package                  → held_packages
#   - apt partial-download artefact → cache_health
#
# Idempotent. Reverse with sudo ./demo/teardown.sh.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "needs root: please run with 'sudo $0'" >&2
    exit 1
fi

UNIT=ubuntu-doctor-demo-fail.service

cat > "/etc/systemd/system/${UNIT}" <<'EOF'
[Unit]
Description=ubuntu-doctor demo: deliberately failing unit

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo "demo failure"; exit 1'
EOF

systemctl daemon-reload
# Expected to fail — that's the point. Suppress the non-zero exit.
systemctl start "${UNIT}" 2>/dev/null || true
echo "  ok  created and started ${UNIT} (now in failed state)"

HOLD_PKG=""
for cand in cowsay sl htop tree neofetch fortune; do
    if dpkg -s "$cand" >/dev/null 2>&1; then
        HOLD_PKG="$cand"
        break
    fi
done
if [[ -n "$HOLD_PKG" ]]; then
    apt-mark hold "$HOLD_PKG" >/dev/null
    echo "  ok  held package: ${HOLD_PKG}"
else
    echo "  -- no candidate package found to hold (tried: cowsay sl htop tree neofetch fortune)"
    echo "     install one of those if you want the held_packages analyzer to fire"
fi

mkdir -p /var/cache/apt/archives/partial
touch /var/cache/apt/archives/partial/ubuntu-doctor-demo.deb.partial
echo "  ok  planted /var/cache/apt/archives/partial/ubuntu-doctor-demo.deb.partial"

echo ""
echo "seed complete."
echo "  next:  ubuntu-doctor"
echo "  undo:  sudo ./demo/teardown.sh"
