#!/usr/bin/env bash
# demo/teardown.sh — undo everything seed.sh planted.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "needs root: please run with 'sudo $0'" >&2
    exit 1
fi

UNIT=doctor-demo-fail.service

systemctl reset-failed "${UNIT}" 2>/dev/null || true
systemctl disable "${UNIT}" 2>/dev/null || true
rm -f "/etc/systemd/system/${UNIT}"
systemctl daemon-reload
echo "  ok  removed ${UNIT}"

for cand in cowsay sl htop tree neofetch fortune; do
    apt-mark unhold "$cand" >/dev/null 2>&1 || true
done
echo "  ok  unheld any demo packages"

rm -f /var/cache/apt/archives/partial/ubuntu-doctor-demo.deb.partial
echo "  ok  removed planted partial download"

echo ""
echo "teardown complete. system is back to its previous state."
