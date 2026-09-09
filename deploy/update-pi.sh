#!/usr/bin/env bash
# Haal de laatste code op en herstart het dashboard.
#   cd ~/kamer-assistente && bash deploy/update-pi.sh
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

git fetch --quiet origin

# ---- lokale wijzigingen? even opzij zetten zodat de pull kan slagen ----
STASHED=0
if [ -n "$(git status --porcelain)" ]; then
  echo "!!  lokale wijzigingen gevonden:"
  git status --short
  STASH_MSG="auto-stash update-pi $(date +%Y-%m-%dT%H:%M:%S)"
  git stash push -u -m "$STASH_MSG" >/dev/null
  STASHED=1
  echo "==> tijdelijk geparkeerd in de stash ($STASH_MSG)"
fi

# ---- pull (fast-forward) — fetch is hierboven al gedaan ----
echo "==> git merge --ff-only origin/main"
if ! git merge --ff-only origin/main; then
  echo
  echo "!!  Kon niet fast-forwarden — je Pi loopt voor op origin/main"
  echo "!!  (lokale commits?). Bekijk 'git log --oneline origin/main..HEAD'."
  echo "!!  Wil je de Pi gelijktrekken met GitHub en lokale commits weggooien:"
  echo "!!      git reset --hard origin/main"
  [ "$STASHED" = 1 ] && echo "!!  Je geparkeerde wijzigingen staan nog in 'git stash list'."
  exit 1
fi

# ---- geparkeerde wijzigingen terugzetten ----
if [ "$STASHED" = 1 ]; then
  echo "==> git stash pop"
  if ! git stash pop; then
    echo "!!  Je lokale wijzigingen botsen met de nieuwe code. Ze staan veilig"
    echo "!!  in 'git stash list'; los het handmatig op met 'git stash pop'."
    exit 1
  fi
fi

if [ -f .venv/bin/pip ]; then
  REQ=requirements-dashboard.txt
  [ -f "$REQ" ] || REQ=requirements.txt
  echo "==> pip install -r $REQ (alleen wijzigingen)"
  ./.venv/bin/pip install -r "$REQ" -q
fi

if systemctl cat kamer-dashboard.service >/dev/null 2>&1; then
  echo "==> sudo systemctl restart kamer-dashboard"
  sudo systemctl restart kamer-dashboard
  sleep 1
  sudo systemctl --no-pager --lines=6 status kamer-dashboard || true
else
  echo "!!  Geen systemd-service gevonden."
  echo "!!  Aanrader: 'bash deploy/setup-pi.sh' (dan herstart 'ie voortaan vanzelf)."
  echo "!!  Nu even handmatig herstarten:"
  echo "!!      fuser -k 5000/tcp ; .venv/bin/python -m rundashboard"
fi
