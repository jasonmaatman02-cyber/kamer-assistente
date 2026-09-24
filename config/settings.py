"""Central configuration.

Two layers:

* **Secrets** live in ``.env`` (git-ignored) and are read with :func:`secret`.
* **Runtime settings** live in ``settings.json`` (git-ignored) and are read /
  written with :func:`get` / :func:`set`. Anything missing there falls back to
  :data:`DEFAULTS`, so the app works on first run with no ``settings.json`` at
  all. The dashboard Settings tab edits this file through ``/api/settings``.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "settings.json"

# --------------------------------------------------------------------------- #
# Defaults — the free / offline stack is the default everywhere.
# --------------------------------------------------------------------------- #
DEFAULTS: dict = {
    "ai": {
        "backend": "ollama",            # ollama | openai
        "ollama_url": "http://localhost:11434",
        "ollama_model": "qwen2.5:1.5b",  # klein & snel genoeg voor een Pi 4B; 'ollama pull' vereist
        "openai_model": "gpt-4o-mini",
        "temperature": 0.6,
        "max_history": 20,
        # leestimeout (s) naar Ollama; dekt de gemeten koude start (~225s) met marge
        "ollama_timeout_s": 330,
    },
    "tts": {
        "backend": "piper",             # piper | espeak | openai | none
        "piper_model": "nl_NL-mls_5809-low",
        "piper_speaker": 0,
        "openai_voice": "onyx",
        "volume": 1.0,
        "language": "nl",
    },
    "stt": {
        "backend": "faster_whisper",    # faster_whisper | openai | whisper
        "model": "tiny",                # tiny | base | small ...
        "compute_type": "int8",
        "language": "nl",
        "device": "cpu",
    },
    "weather": {
        "provider": "open-meteo",       # open-meteo (no key) | weatherapi
        "city": "Arnhem",
        "latitude": 51.985,
        "longitude": 5.899,
    },
    "search": {
        "provider": "duckduckgo",       # duckduckgo (no key) | serper
        "max_results": 5,
    },
    "spotify": {
        "market": "NL",                 # land voor beschikbaarheid van nummers
        # Naam waarmee de Pi's eigen Spotify Connect-apparaat (raspotify/
        # librespot) adverteert -- zie Dashboard/backend/services.py::
        # active_device_id(). Alleen gebruikt als FALLBACK wanneer er
        # nergens al actief gespeeld wordt (nooit om een lopende sessie
        # elders over te nemen).
        "pi_device_name": "Kamer-AI",
    },
    "camera": {
        "enabled": True,
        # "auto" -> USB/opencv, val terug op de Pi-lintkabelcamera (picamera2)
        "backend": "auto",
        "device_index": 0,
        "width": 640,
        "height": 360,
        "fps": 10,
        "jpeg_quality": 55,
        "max_viewers": 3,               # gelijktijdige /video_feed-streams (elk = 1 serverthread)
        "browser_detection": False,     # run coco-ssd in the browser (heavy, off by default)
        # min. zekerheid (0-1) om iemand als "persoon" te tellen. Lager = pikt
        # ook wazig IR-/nachtbeeld op, maar meer valse detecties.
        "detect_threshold": 0.5,
        # in dit venster laat detectie de lamp NIET automatisch aangaan.
        # from == to (of allebei leeg) = altijd toegestaan, ook 's nachts.
        "lamp_quiet_from": "21:40",
        "lamp_quiet_to": "07:00",
    },
    "dashboard": {
        "poll_now_playing_ms": 4000,
        "poll_devices_ms": 15000,
        "poll_weather_ms": 1200000,
        "poll_agenda_ms": 300000,
        "poll_system_ms": 10000,
    },
    "alarm": {"time": "", "routine": "morning"},   # handmatige dashboard-wekker
    "agenda": {
        # Beide account-slots hebben een expliciet instelbare provider --
        # niet aangenomen dat slot 1 per definitie iCloud is, want welk
        # e-mailadres in welk veld staat is een Settings-detail, geen
        # architectuurregel. "icloud" | "google" | "" (dat account uit).
        "account1_provider": "icloud",
        "account2_provider": "google",
    },
    "presence": {
        # server-side (Pi-native) mensen-tellen via de bestaande camera, drijft
        # de automatische lamp aan. Los van 'camera.browser_detection' (dat is
        # client-side, in de browser van wie er kijkt — deze draait altijd,
        # ongeacht of iemand het dashboard open heeft staan).
        "enabled": False,
        "interval_s": 3.0,             # PEOPLE_DETECTION_INTERVAL: hoe vaak een frame checken
        "consecutive_required": 2,     # zoveel opeenvolgende positieve detecties voor OCCUPIED
        # Frame verkleinen vóór HOG-detectie (0.25-1.0). 1.0 = ongewijzigd. Lager
        # = veel minder CPU (dev-meting: 0.6 ~10x sneller) maar mogelijk minder
        # gevoelig voor verre/kleine personen -- eerst op het echte beeld toetsen.
        "detect_scale": 1.0,
        "empty_grace_s": 20.0,         # zo lang wachten met EMPTY na de laatste detectie
        "auto_light_enabled": False,   # AUTO_LIGHT_ENABLED
        "lamp": 0,                     # welke lamp (index/naam) de presence-logica bedient
        # AUTO_LIGHT_BLOCK_AFTER: harde regel — na dit tijdstip mag de lamp
        # nooit meer automatisch AAN (uitzetten mag wel). "" = geen blokkade.
        "auto_light_block_after": "21:30",
    },
    "security": {
        # 0 = elke browsersessie opnieuw inloggen (cookie weg bij afsluiten),
        # met een harde serverlimiet van 24u. >0 = zoveel dagen onthouden.
        "session_days": 0,
    },
    "devices": {
        "lamps": [
            {"name": "Bedroom Lamp", "ip": "192.168.2.15"},
            {"name": "Desk Lamp", "ip": "192.168.3.19"},
        ],
        "thermostat": {"target": 20, "min": 15, "max": 30},
        # Timeout (s) per Tapo-aanroep; zonder timeout duurt een offline lamp ~21s.
        "lamp_timeout_s": 6,
    },
    "features": {
        "voice_assistant": True,
        "spotify": True,
        "radio": True,
        "calendar": True,
        "email": True,
        "camera": True,
    },
    "assistant": {
        "name": "Kamer",
        "wake_words": ["hey kamer", "hey kammer", "hey camera", "hoi kamer"],
        # "auto"  -> Porcupine als 't kan (pvporcupine + .ppn + PICOVOICE_ACCESS_KEY),
        #            anders whisper-transcriptie op de wake-woorden.
        # "porcupine" / "whisper" forceren een backend.
        "wake_backend": "auto",
        "porcupine_keyword": "voice/hey_kamer.ppn",
        "porcupine_sensitivity": 0.5,
        "system_prompt": (
            "Je bent een Nederlandse spraakassistent voor kamerautomatisering. "
            "Regels:\n"
            "1. Kies ALTIJD de meest specifieke functie. Voor weer -> haal_weer_op / "
            "haal_temp_op / haal_wind_op. Voor lampen -> zet_lamp. Voor muziek/radio -> "
            "speel_muziek / speel_radio. Voor agenda -> afspraken_vandaag / afspraken_morgen. "
            "Voor notities -> voeg_notitie_toe / lees_notities.\n"
            "2. Gebruik 'zoek_internet' ALLEEN als geen enkele andere functie past en je "
            "echt actuele externe informatie nodig hebt.\n"
            "3. Kun je iets zelf beantwoorden (rekenen, algemene kennis, uitleg), doe dat "
            "gewoon zonder functie.\n"
            "4. Antwoord kort en in het Nederlands."
        ),
    },
}

_lock = threading.RLock()
_cache: dict | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        user: dict = {}
        if SETTINGS_FILE.exists():
            try:
                user = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[config] kon settings.json niet lezen: {exc}")
        _cache = _deep_merge(DEFAULTS, user)
        return _cache


def reload() -> dict:
    """Drop the in-memory cache and re-read ``settings.json``."""
    global _cache
    with _lock:
        _cache = None
    return _load()


def all_settings() -> dict:
    """Return the full, merged settings tree (safe to serialise to JSON)."""
    return copy.deepcopy(_load())


def get(path: str, default=None):
    """Read a dotted path, e.g. ``get("camera.fps")``.

    Dict/list-waarden worden gekopieerd teruggegeven zodat een aanroeper de
    in-memory cache niet per ongeluk kan muteren. Scalars (het gros van de
    aanroepen: fps, ms, booleans, strings) gaan zonder overhead."""
    node = _load()
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return copy.deepcopy(node) if isinstance(node, (dict, list)) else node


def set(path: str, value) -> None:
    """Set a dotted path in memory and persist to ``settings.json``."""
    with _lock:
        tree = _load()
        node = tree
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        _persist(tree)


def update(patch: dict) -> dict:
    """Deep-merge ``patch`` into the current settings and persist."""
    global _cache
    with _lock:
        merged = _deep_merge(_load(), patch)
        _cache = merged
        _persist(merged)
        return copy.deepcopy(merged)


def save() -> None:
    with _lock:
        _persist(_load())


def _persist(tree: dict) -> None:
    # Only write the diff against DEFAULTS so settings.json stays readable.
    diff = _diff(DEFAULTS, tree)
    try:
        # Atomisch schrijven (tmp-bestand + os.replace) i.p.v. direct naar
        # settings.json -- write_text() opent met truncate-then-write, dus
        # een onderbreking halverwege (stroomuitval, kill -9, volle schijf)
        # kan een leeg/kapot settings.json achterlaten. Alle instellingen
        # (camera-drempel, 21:30-regel, wake-woorden, AI-model, ...) vallen
        # dan bij de volgende load() stil terug op DEFAULTS. os.replace() is
        # op zowel Linux als Windows een atomaire rename, geen tussenstaat
        # mogelijk. Zelfde patroon als logic/notes.py::save_notes().
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    except OSError as exc:
        print(f"[config] kon settings.json niet schrijven: {exc}")


def _diff(base: dict, current: dict) -> dict:
    out: dict = {}
    for key, cur in current.items():
        b = base.get(key)
        if isinstance(cur, dict) and isinstance(b, dict):
            sub = _diff(b, cur)
            if sub:
                out[key] = sub
        elif cur != b:
            out[key] = cur
    return out


ENV_FILE = BASE_DIR / ".env"

# Secrets the dashboard's Settings tab may edit. The "public" ones (emails,
# hostnames) are shown in full; the rest are only ever shown masked.
SECRET_KEYS = [
    "DASHBOARD_PASSWORD",
    "OPENAI_API_KEY",
    "TAPO_USER", "TAPO_PASSWORD",
    "EMAIL_ADDRESS", "EMAIL_PASSWORD", "SMTP_SERVER", "SMTP_PORT", "RECEIVER",
    "APPLE_ID_1", "APPLE_PASSWORD_1", "APPLE_ID_2", "APPLE_PASSWORD_2",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI",
    "OPENWEATHER_KEY", "WEATHERAPI_KEY", "SERPER_API_KEY",
    "PICOVOICE_ACCESS_KEY",
]
_PUBLIC_SECRETS = {
    "TAPO_USER", "EMAIL_ADDRESS", "SMTP_SERVER", "SMTP_PORT", "RECEIVER",
    "APPLE_ID_1", "APPLE_ID_2", "GOOGLE_CLIENT_ID", "GOOGLE_REDIRECT_URI",
    "SPOTIFY_CLIENT_ID", "SPOTIFY_REDIRECT_URI",
}


def secret(name: str, default: str = "") -> str:
    """Read a secret from the environment (``.env``)."""
    return os.environ.get(name, default)


def set_secret(name: str, value: str) -> None:
    """Write a secret to ``.env`` and apply it immediately (no restart)."""
    value = (value or "").strip()
    with _lock:
        ENV_FILE.touch(exist_ok=True)
        try:
            if value:
                from dotenv import set_key

                # quote_mode="always": zet 'value' tussen quotes zodat een
                # wachtwoord met #, $, spaties enz. niet corrupt terugleest.
                set_key(str(ENV_FILE), name, value, quote_mode="always")
                os.environ[name] = value
            else:
                from dotenv import unset_key

                unset_key(str(ENV_FILE), name)
                os.environ.pop(name, None)
        except Exception as exc:  # noqa: BLE001
            print(f"[config] kon secret '{name}' niet opslaan: {exc}")
            raise


def _mask(value: str) -> str:
    if len(value) <= 6:
        return "••••"
    return f"{value[:3]}…{value[-2:]}"


def secret_status() -> dict:
    """``{name: {"set": bool, "hint": str}}`` — never the raw secret value."""
    out = {}
    for key in SECRET_KEYS:
        val = os.environ.get(key, "")
        if not val:
            out[key] = {"set": False, "hint": ""}
        elif key in _PUBLIC_SECRETS:
            out[key] = {"set": True, "hint": val}
        else:
            out[key] = {"set": True, "hint": _mask(val)}
    return out
