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
    for key in config.SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    config.reload()
    config.set("tts.backend", "none")
    # verse service-registry per test
    from Dashboard.backend import services

    services._services.clear()
    services._errors.clear()
    services._health_cache.clear()

    # verse chat-sessies per test
    from logic import gpt_handler

    gpt_handler._sessions.clear()


@pytest.fixture()
def client():
    from Dashboard.backend.main import app

    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c
