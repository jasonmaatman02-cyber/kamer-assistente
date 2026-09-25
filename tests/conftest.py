"""Test fixtures. Keep everything offline: no real Spotify / Tapo / mail."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# geen echte audio / secrets in tests
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """No real secrets -> services can't reach Spotify / iCloud / Gmail."""
    import config

    monkeypatch.setattr("config.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("config.settings.ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr("logic.notes.NOTES_FILE", tmp_path / "notes.json")
    monkeypatch.setattr("logic.logger.BASE_LOG_DIR", tmp_path / "logs")  # niet in de echte data/logs schrijven
    monkeypatch.setattr("scheduler.agenda.GOOGLE_TOKEN_FILE", tmp_path / "google_calendar_token.json")
    for key in config.SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    config.reload()
    config.set("tts.backend", "none")
    # Ollama draait op de Pi en op sommige dev-machines echt: een test die "Ollama is uit" wil, moest daar een
    # echt modelantwoord krijgen (en belastte de Pi met een echte generatie). Poort 9 (discard) weigert altijd.
    config.set("ai.ollama_url", "http://127.0.0.1:9")
    # verse service-registry per test
    from Dashboard.backend import services

    services._services.clear()
    services._errors.clear()
    services._health_cache.clear()
    services._data_cache.clear()
    services._data_cache_locks.clear()
    services._lamp_conns.clear()
    services._lamp_fail_at.clear()
    # Geen echte mDNS-lookup (2s timeout) in tests -- zelfde "alles offline"
    # filosofie als de rest van deze fixture. Tests die de Pi-via-mDNS-
    # discovery zelf willen testen overschrijven dit met hun eigen
    # monkeypatch.setattr(services, "pi_spotify_device", ...).
    monkeypatch.setattr(services, "pi_spotify_device", lambda fresh=False: None)

    # verse chat-sessies per test
    from logic import gpt_handler

    gpt_handler._sessions.clear()


@pytest.fixture()
def client():
    from Dashboard.backend.main import app

    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


@pytest.fixture()
def amsterdam_summer(monkeypatch):
    """De lokale tijd van de Pi (UTC+2 in september) als vast verschil, onafhankelijk van de tijdzone van de
    machine waarop de tests draaien: de CI staat op UTC en een ontwikkel-pc kan er weer anders voor staan.
    Tests die 'lokale' klokslagen van Google-events controleren, gebruiken deze fixture."""
    import datetime

    import scheduler.agenda as agenda_mod

    tz = datetime.timezone(datetime.timedelta(hours=2), "CEST")
    monkeypatch.setattr(agenda_mod, "_local_tz", lambda: tz)
    return tz


_LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Tests horen nooit het echte netwerk op te gaan (Spotify, Google, Open-Meteo, DuckDuckGo, de lampen, een
    lokale Ollama...). Loopback (eigen testservers) blijft toegestaan; al het andere geeft direct een fout,
    zodat een vergeten mock zichtbaar wordt in plaats van stilletjes te werken (of te falen op een machine
    zonder internet)."""
    import socket

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in _LOCAL_HOSTS and not str(host).startswith("127."):
            raise socket.gaierror(f"netwerk geblokkeerd in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
            host = address[0]
            if host not in _LOCAL_HOSTS and not str(host).startswith("127."):
                raise OSError(f"netwerk geblokkeerd in tests: {host!r}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
