#!/usr/bin/env bash
# Haal de laatste code op en herstart het dashboard.
#   cd ~/kamer-assistente && bash deploy/update-pi.sh
#
# Alles staat in main(): bash leest een script incrementeel, en dit script wordt door de 'git merge'
# hieronder zelf vervangen. Zonder de functie kon een gewijzigd update-pi.sh halverwege de uitvoering
# op verkeerde offsets doorlezen; nu wordt het hele blok eerst geparsed en dan pas uitgevoerd.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

main() {

  git fetch --quiet origin
  PREV="$(git rev-parse --short HEAD)"

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

  # 'systemctl cat' leest het unit-bestand zelf en faalt met Permission denied
  # als dat niet wereld-leesbaar is (bv. 600 i.p.v. 644 na een 'sudo cp') — ook
  # als de service prima draait. 'is-enabled'/'is-active' vragen de daemon zelf
  # via de systemd-bus, dat werkt altijd zonder sudo. Exit 4 = unit bestaat niet.
  if systemctl is-enabled kamer-dashboard.service >/dev/null 2>&1 \
     || systemctl is-active kamer-dashboard.service >/dev/null 2>&1; then
    echo "==> sudo systemctl restart kamer-dashboard"
    sudo systemctl restart kamer-dashboard
    sleep 1
    sudo systemctl --no-pager --lines=6 status kamer-dashboard || true

    # Health-check: liever nu een duidelijke fout dan een stil kapot dashboard op
    # een headless Pi (bv. na een mislukte dependency of een syntaxfout).
    # /api/config heeft geen externe afhankelijkheden, dus antwoordt snel.
    echo "==> wachten tot het dashboard antwoordt (max 40s)"
    ok=0
    for _ in $(seq 1 40); do
      if curl -fsS --max-time 2 http://127.0.0.1:5000/api/config >/dev/null 2>&1; then ok=1; break; fi
      sleep 1
    done
    if [ "$ok" = 1 ]; then
      echo "==> dashboard OK (nu op $(git rev-parse --short HEAD), was $PREV)"
      # De systemd-unit wordt alleen door setup-pi.sh vernieuwd; zonder WatchdogSec herstart systemd
      # een VASTGELOPEN (niet gecrasht) dashboard niet.
      wd="$(systemctl show kamer-dashboard.service -p WatchdogUSec --value 2>/dev/null || true)"
      if [ -z "$wd" ] || [ "$wd" = "0" ] || [ "$wd" = "infinity" ]; then
        echo "==> tip: draai 'bash deploy/setup-pi.sh' eenmalig om de systemd-watchdog te activeren"
      fi
    else
      echo
      echo "!!  Het dashboard antwoordt niet na de update. Laatste logregels:"
      journalctl -u kamer-dashboard -n 25 --no-pager || true
      echo "!!  Terugdraaien naar de vorige versie ($PREV):"
      echo "!!      git reset --hard $PREV && sudo systemctl restart kamer-dashboard"
      exit 1
    fi
  else
    echo "!!  Geen systemd-service gevonden."
    echo "!!  Aanrader: 'bash deploy/setup-pi.sh' (dan herstart 'ie voortaan vanzelf)."
    echo "!!  Nu even handmatig herstarten:"
    echo "!!      fuser -k 5000/tcp ; .venv/bin/python -m rundashboard"
  fi
}

main "$@"
exit $?
