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
    },
    "camera": {
        "enabled": True,
        "device_index": 0,
        "width": 640,
        "height": 360,
        "fps": 10,
        "jpeg_quality": 55,
        "browser_detection": False,     # run coco-ssd in the browser (heavy, off by default)
    },
    "dashboard": {
        "poll_now_playing_ms": 4000,
        "poll_devices_ms": 15000,
        "poll_weather_ms": 1200000,
        "poll_agenda_ms": 300000,
        "poll_system_ms": 10000,
    },
    "devices": {
        "lamps": [
            {"name": "Bedroom Lamp", "ip": "192.168.2.15"},
            {"name": "Desk Lamp", "ip": "192.168.3.19"},
        ],
        "thermostat": {"target": 20, "min": 15, "max": 30},
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
    """Read a dotted path, e.g. ``get("camera.fps")``."""
    node = _load()
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


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
        SETTINGS_FILE.write_text(
            json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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
    "OPENAI_API_KEY",
    "TAPO_USER", "TAPO_PASSWORD",
    "EMAIL_ADDRESS", "EMAIL_PASSWORD", "SMTP_SERVER", "SMTP_PORT", "RECEIVER",
    "APPLE_ID_1", "APPLE_PASSWORD_1", "APPLE_ID_2", "APPLE_PASSWORD_2",
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI",
    "OPENWEATHER_KEY", "WEATHERAPI_KEY", "SERPER_API_KEY",
]
_PUBLIC_SECRETS = {
    "TAPO_USER", "EMAIL_ADDRESS", "SMTP_SERVER", "SMTP_PORT", "RECEIVER",
    "APPLE_ID_1", "APPLE_ID_2", "SPOTIFY_CLIENT_ID", "SPOTIFY_REDIRECT_URI",
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

                set_key(str(ENV_FILE), name, value, quote_mode="never")
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
