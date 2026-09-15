#!/usr/bin/env bash
# Eenmalige setup van de kamer-assistent op een Raspberry Pi (Bookworm / Pi OS).
# Idempotent: je kan 'm zonder schade opnieuw draaien.
#
#   cd ~/kamer-assistente && bash deploy/setup-pi.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${SUDO_USER:-$(id -un)}"
PY_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
cd "$REPO_DIR"

echo "==> repo:    $REPO_DIR"
echo "==> user:    $SERVICE_USER"
echo "==> model:   $PY_MODEL"
echo

# ---------------------------------------------------------------- systeem-pakketten
echo "==> apt-pakketten"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  python3-venv python3-dev \
  libvlc-dev vlc \
  espeak-ng \
  portaudio19-dev libopenblas0 \
  logrotate \
  avahi-daemon   # -> pi bereikbaar op <hostname>.local, geen vast IP nodig

# ---------------------------------------------------------------- raspotify (Spotify Connect)
# Maakt van de Pi zelf een gewoon Spotify Connect-apparaat (zoals een Chromecast/
# Sonos) -- de standaardoplossing hiervoor op een Pi, geen eigen streaming-
# implementatie nodig. Zodra je 'm één keer in de Spotify-app kiest (Connect-
# icoon), verschijnt 'ie vanzelf in het bestaande apparaatlijstje van het
# dashboard (/api/devices, Media-pagina) -- die bestuurt elk Spotify Connect-
# apparaat al generiek, dus daar hoeft niets voor bij te veranderen.
if ! command -v raspotify >/dev/null 2>&1 && ! systemctl list-unit-files raspotify.service >/dev/null 2>&1; then
  echo "==> raspotify installeren (Spotify Connect voor de Pi zelf)"
  curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
else
  echo "==> raspotify al geinstalleerd"
fi

if sudo test -f /etc/raspotify/conf; then
  echo "==> raspotify configureren (/etc/raspotify/conf)"
  # Naam waaronder de Pi in de Spotify-app verschijnt. Wil je een andere naam,
  # pas 'm gewoon aan in /etc/raspotify/conf en herstart: sudo systemctl restart raspotify
  # (bestand is niet wereld-leesbaar -- vandaar overal sudo, ook voor de check hierboven)
  if sudo grep -q '^#\?LIBRESPOT_NAME=' /etc/raspotify/conf; then
    sudo sed -i 's/^#\?LIBRESPOT_NAME=.*/LIBRESPOT_NAME="Kamer-AI"/' /etc/raspotify/conf
  else
    echo 'LIBRESPOT_NAME="Kamer-AI"' | sudo tee -a /etc/raspotify/conf >/dev/null
  fi
  # Audio-uitvoer bewust NIET vastgepind op een specifieke ALSA-device (net als
  # de rest van dit project -- TTS/radio gebruiken ook gewoon de systeem-default);
  # werkt het geluid op deze Pi niet via de gewenste uitgang, stel LIBRESPOT_DEVICE
  # in /etc/raspotify/conf in aan de hand van `aplay -l`.

  # raspotify's eigen unit heeft automatisch herstarten bij een crash
  # standaard UITgeschakeld (in commentaar in hun package). Expliciet aanzetten
  # via een drop-in i.p.v. hun unit-bestand zelf aan te passen (overleeft een
  # 'apt upgrade' van raspotify zelf, en blijft onafhankelijk van het dashboard).
  sudo mkdir -p /etc/systemd/system/raspotify.service.d
  sudo tee /etc/systemd/system/raspotify.service.d/override.conf >/dev/null <<'EOF'
[Service]
Restart=on-failure
RestartSec=10
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now raspotify
  echo "==> raspotify status:"
  sudo systemctl --no-pager --lines=6 status raspotify || true
else
  echo "!!  /etc/raspotify/conf niet gevonden -- raspotify-installatie lijkt mislukt, sla configuratie over."
fi

# ---------------------------------------------------------------- python venv
if [ ! -d .venv ]; then
  echo "==> venv aanmaken"
  python3 -m venv .venv
fi
echo "==> pip install (dit kan op trage wifi even duren)"
./.venv/bin/pip install --upgrade pip -q
REQ=requirements-dashboard.txt
[ -f "$REQ" ] || REQ=requirements.txt
./.venv/bin/pip install -r "$REQ" -q
./.venv/bin/pip install waitress -q

# ---------------------------------------------------------------- .env
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo
  echo "!!  .env aangemaakt vanaf .env.example."
  echo "!!  Vul minstens EMAIL_ADDRESS / EMAIL_PASSWORD / RECEIVER in met 'nano .env'."
  echo "!!  De rest (Spotify, Tapo, OpenAI, Apple) kan daarna via Settings in de UI."
  echo
else
  chmod 600 .env
  echo "==> .env bestaat al (rechten op 600 gezet)"
fi

# ---------------------------------------------------------------- ollama (optioneel)
if command -v ollama >/dev/null 2>&1; then
  echo "==> ollama al geinstalleerd"
else
  read -r -p "==> Ollama (lokale LLM) nu installeren? [J/n] " ans
  if [[ "${ans:-J}" =~ ^[JjYy]?$ ]]; then
    curl -fsSL https://ollama.com/install.sh | sh
  fi
fi
if command -v ollama >/dev/null 2>&1; then
  echo "==> ollama pull $PY_MODEL"
  ollama pull "$PY_MODEL" || echo "!!  pull mislukt (geen net?) — later: ollama pull $PY_MODEL"
fi

# Ollama's eigen default (OLLAMA_KEEP_ALIVE=5m) ontlaadt het model uit het
# geheugen na 5 minuten inactiviteit. Op een gedeelde server is dat prima,
# maar deze Pi draait Ollama uitsluitend voor Kamer-AI zelf -- het geheugen
# hoeft nergens anders voor vrijgemaakt te worden. Zonder deze override
# betaalt bijna elk "eerste bericht na een tijdje" de volledige herlaad- +
# ongecachte-prompt-kosten opnieuw (live gemeten: ~4 minuten op een Pi 4B
# met qwen2.5:1.5b), wat in de praktijk precies de AI-timeouts veroorzaakte.
if systemctl list-unit-files ollama.service >/dev/null 2>&1; then
  echo "==> ollama: model warm houden (OLLAMA_KEEP_ALIVE=24h, i.p.v. de 5 min. default)"
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_KEEP_ALIVE=24h"
EOF
  sudo systemctl daemon-reload
  sudo systemctl restart ollama
fi

# ---------------------------------------------------------------- logrotate
echo "==> logrotate-regel voor data/logs"
sudo tee /etc/logrotate.d/kamer-assistente >/dev/null <<EOF
$REPO_DIR/data/logs/*/*.txt {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF

# ---------------------------------------------------------------- systemd service
echo "==> systemd service installeren"
TMP_UNIT="$(mktemp)"
sed -e "s|/home/pi/kamer-assistente|$REPO_DIR|g" \
    -e "s|^User=pi$|User=$SERVICE_USER|" \
    deploy/kamer-dashboard.service > "$TMP_UNIT"
sudo cp "$TMP_UNIT" /etc/systemd/system/kamer-dashboard.service
sudo chmod 644 /etc/systemd/system/kamer-dashboard.service   # mktemp geeft 600 mee; cp behoudt dat -> systemctl cat faalt dan zonder sudo
rm -f "$TMP_UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now kamer-dashboard.service

echo
echo "==> klaar. Status:"
sudo systemctl --no-pager --lines=8 status kamer-dashboard.service || true
echo
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "Dashboard:  http://${IP:-<pi-ip>}:5000   of   http://$(hostname).local:5000"
echo "Logs:       journalctl -u kamer-dashboard -f"
echo "Herstart:   sudo systemctl restart kamer-dashboard"
echo
echo "Spotify Connect (raspotify): kies 'Kamer-AI' in het Connect-icoon van de Spotify-app,"
echo "               daarna verschijnt 'ie vanzelf in het dashboard onder Media -> Apparaten."
echo "Logs:       journalctl -u raspotify -f"
echo "Herstart:   sudo systemctl restart raspotify"
