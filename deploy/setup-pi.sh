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
  logrotate

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
rm -f "$TMP_UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now kamer-dashboard.service

echo
echo "==> klaar. Status:"
sudo systemctl --no-pager --lines=8 status kamer-dashboard.service || true
echo
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "Dashboard:  http://${IP:-<pi-ip>}:5000"
echo "Logs:       journalctl -u kamer-dashboard -f"
echo "Herstart:   sudo systemctl restart kamer-dashboard"
