#!/usr/bin/env bash
# Haal de laatste code op en herstart het dashboard.
#   cd ~/kamer-assistente && bash deploy/update-pi.sh
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> git pull"
git pull --ff-only

if [ -f .venv/bin/pip ]; then
  REQ=requirements-dashboard.txt
  [ -f "$REQ" ] || REQ=requirements.txt
  echo "==> pip install -r $REQ (alleen wijzigingen)"
  ./.venv/bin/pip install -r "$REQ" -q
fi

if systemctl list-unit-files 2>/dev/null | grep -q '^kamer-dashboard\.service'; then
  echo "==> systemctl restart kamer-dashboard"
  sudo systemctl restart kamer-dashboard
  sleep 1
  sudo systemctl --no-pager --lines=5 status kamer-dashboard || true
else
  echo "!!  geen systemd-service gevonden — draai deploy/setup-pi.sh, of herstart handmatig."
fi
