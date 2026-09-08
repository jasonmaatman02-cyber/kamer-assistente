# Kamerproject — slimme kamer + dashboard

Draait op een Raspberry Pi 4B (4 GB). De hele AI-stack is **gratis en lokaal**:
chat via Ollama, spraak via Piper, transcriptie via faster-whisper, weer via
Open-Meteo, internet zoeken via DuckDuckGo. OpenAI is alleen nog een optionele
fallback.

## ⚠️ Eerst: roteer je secrets

`keys/API_keys.py` stond vol met echte wachtwoorden en API-keys in platte tekst.
Die zijn nu verplaatst naar `.env` (git-ignored), maar ze zijn al gelekt —
**vervang ze allemaal**:

| Key | Waar |
|-----|------|
| OpenAI API key | https://platform.openai.com/api-keys → oude intrekken |
| Tapo / TP-Link wachtwoord | TP-Link account — en gebruik **niet** je Google-wachtwoord |
| Gmail app-password | https://myaccount.google.com/apppasswords → oude verwijderen |
| Apple app-wachtwoorden (2×) | https://account.apple.com → Inloggen & beveiliging |
| Spotify client secret | https://developer.spotify.com/dashboard → "Rotate secret" |
| WeatherAPI / Serper keys | niet meer nodig (gratis providers), mag je intrekken |

Zet de nieuwe waarden in `.env` (kopieer `.env.example` als basis).
`.cache` (Spotify-token) is ook git-ignored; verwijderen forceert opnieuw
inloggen.

## Installatie op de Pi

```bash
sudo apt install python3-venv libvlc-dev vlc espeak-ng portaudio19-dev libopenblas0
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # of requirements-dashboard.txt

# Ollama (lokale LLM)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b                  # ~1 GB, snel genoeg op een Pi 4B
```

Piper- en faster-whisper-modellen worden bij het eerste gebruik automatisch
gedownload naar `models/` resp. de HuggingFace-cache.

## Starten

```bash
python main.py dashboard     # alleen het webdashboard  → http://<pi-ip>:5000
python main.py assistant     # alleen de spraakassistent
python main.py all           # allebei
```

Herstart de server na code-wijzigingen (Ctrl+C en opnieuw starten) — een oude
draaiende instantie serveert nog de oude pagina's.

### Als service (aanrader op de Pi)

`screen` is fragiel: valt de Pi uit, dan is het dashboard weg. Beter is een
systemd-service die automatisch (her)start:

```bash
cd ~/kamer-assistente
bash deploy/setup-pi.sh        # apt-pakketten, venv, .env, logrotate, service
```

Daarna:

```bash
bash deploy/update-pi.sh       # git pull + pip + systemctl restart
journalctl -u kamer-dashboard -f
sudo systemctl restart kamer-dashboard
```

De service draait `python -m rundashboard` (waitress) als user `pi`, met
`Restart=always` en een geheugenlimiet van 1,2 GB.

## Instellingen

Alles is in te stellen via de **Settings**-tab in het dashboard (schrijft naar
`settings.json`). Handig voor de Pi / trage wifi:

- **Camera**: resolutie 640×360, FPS 8–10, JPEG-kwaliteit 40–60. "Personen­
  detectie in browser" staat uit (scheelt ~4 MB download + CPU).
- **Dashboard verversen**: hogere ms-waarden = minder verkeer en minder
  Spotify-API-calls. De Overview-pagina haalt alles in één `/api/overview`-call op.
- **AI → Ollama model**: `qwen2.5:1.5b` (standaard) is snel genoeg op een
  Pi 4B. Groter (`llama3.2:3b`) = slimmer maar trager.

### Wake word (optioneel)

Standaard luistert de assistent door elke ~2 s een stukje met whisper te
transcriberen en op de wake-woorden te matchen — werkt, maar kost constant CPU.
Lichter is **Porcupine**: `pip install pvporcupine`, haal een gratis AccessKey op
bij [console.picovoice.ai](https://console.picovoice.ai/), zet die als
`PICOVOICE_ACCESS_KEY` (Settings → Inloggegevens) en leg een Nederlandse
"hey kamer" `.ppn` neer als `voice/hey_kamer.ppn`. `assistant.wake_backend`
staat op `auto` en schakelt dan vanzelf over; `whisper`/`porcupine` forceren.

### Inloggegevens via de Settings-tab

API-keys en wachtwoorden (OpenAI, Tapo, Gmail, Apple, Spotify, …) vul je in
onder **Settings → Inloggegevens**. Dat schrijft naar `.env` en past het direct
toe (geen herstart). Waarden komen nooit terug naar de browser — je ziet alleen
een gemaskeerde hint of "niet ingesteld".

De sectie zit achter een slot: je vraagt een 6-cijferige code aan, die naar je
`RECEIVER`-mailadres wordt gestuurd, en daarmee ontgrendel je 30 minuten.

**Eenmalig op een verse Pi**: de mailcode kan pas verstuurd worden als de
Gmail-gegevens al bekend zijn. Vul die dus één keer met de hand in:

```bash
cp .env.example .env
nano .env    # zet EMAIL_ADDRESS, EMAIL_PASSWORD (Gmail app-password) en RECEIVER
```

Daarna doe je al het andere (Spotify, Tapo, OpenAI, Apple) via de UI.

**Spotify** heeft daarna nog een koppelstap: knop *Verbind met Spotify* →
inloggen → de URL waar je op uitkomt terugplakken. Dat cachet het token in
`.cache`.

## Architectuur

```
config/          .env + settings.json, met defaults (config.get / config.set)
ai/              llm.py · tts.py · stt.py  — backend-onafhankelijke AI-helpers
Dashboard/       Flask-app; services worden lazy geladen zodat één storing
                 niet het hele dashboard sloopt
logic/ voice/ devices/ sound_system/ scheduler/ weer/   — losse modules
```

## Bekende beperkingen

- Een lokale LLM op een Pi 4B is traag. Voor snelle spraakbediening kan een
  klein model (`qwen2.5:1.5b`) of OpenAI als backend beter zijn.
- De `Devices`-pagina thermostaat is nog een demo (geen hardware-koppeling).
- Spotify-afspeelbediening vereist een actief apparaat; installeer `raspotify`
  om de Pi zelf een speler te maken.

## Tests

```bash
pip install -r requirements-dashboard.txt pytest requests
pytest -q
```

De tests draaien volledig offline (geen echte Spotify/Tapo/mail — zie
`tests/conftest.py`). CI draait ze bij elke push (`.github/workflows/ci.yml`).
