import config


def test_defaults_load():
    assert config.get("ai.backend") == "ollama"
    assert config.get("camera.fps") == 10
    assert config.get("nope.nope", "x") == "x"


def test_set_persists_only_diff(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.SETTINGS_FILE", tmp_path / "s.json")
    config.reload()
    config.set("camera.fps", 12)
    assert config.get("camera.fps") == 12
    saved = (tmp_path / "s.json").read_text()
    assert '"fps": 12' in saved and "ollama" not in saved  # alleen de diff
    config.set("camera.fps", 10)
    assert (tmp_path / "s.json").read_text().strip() == "{}"


def test_secret_status_masks(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-verysecretvalue123")
    monkeypatch.setenv("EMAIL_ADDRESS", "me@example.com")
    st = config.secret_status()
    assert st["OPENAI_API_KEY"]["set"] is True
    assert "verysecret" not in st["OPENAI_API_KEY"]["hint"]
    assert st["EMAIL_ADDRESS"]["hint"] == "me@example.com"  # publiek -> volledig
