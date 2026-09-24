import json

import pytest

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


def test_get_returns_a_copy_not_the_cache():
    lamps = config.get("devices.lamps")
    lamps.append({"name": "hack", "ip": "0.0.0.0"})
    lamps[0]["ip"] = "9.9.9.9"
    # de cache mag niet meegemuteerd zijn
    fresh = config.get("devices.lamps")
    assert all(e["name"] != "hack" for e in fresh)
    assert fresh[0]["ip"] != "9.9.9.9"


def test_persist_is_atomic_original_file_survives_a_failed_write(tmp_path, monkeypatch):
    """_persist() moet schrijven via een tmp-bestand + os.replace(), niet
    direct naar settings.json -- anders kan een onderbreking halverwege
    (stroomuitval, kill -9) een leeg/kapot settings.json achterlaten en
    valt de HELE configuratie stil terug op DEFAULTS. Simuleert een crash
    tijdens het schrijven van het tmp-bestand en controleert dat het
    bestaande settings.json dan volledig intact blijft."""
    import config.settings as settings_mod

    settings_file = tmp_path / "s.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", settings_file)
    config.reload()
    config.set("camera.fps", 12)
    original = settings_file.read_text()
    assert '"fps": 12' in original

    from pathlib import Path

    real_write_text = Path.write_text

    def boom_write_text(self, *a, **kw):
        if self == settings_file.with_suffix(".json.tmp"):
            raise OSError("gesimuleerde crash tijdens schrijven")
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", boom_write_text)
    config.set("camera.fps", 99)  # _persist() mag hier intern falen, niet crashen

    assert settings_file.read_text() == original, \
        "een mislukte write mag het bestaande settings.json niet aanraken"
    assert not settings_file.with_suffix(".json.tmp").exists(), \
        "geen half geschreven tmp-bestand mag blijven liggen"


def test_secret_status_masks(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-verysecretvalue123")
    monkeypatch.setenv("EMAIL_ADDRESS", "me@example.com")
    st = config.secret_status()
    assert st["OPENAI_API_KEY"]["set"] is True
    assert "verysecret" not in st["OPENAI_API_KEY"]["hint"]
    assert st["EMAIL_ADDRESS"]["hint"] == "me@example.com"  # publiek -> volledig


# --- kapot settings.json ------------------------------------------------- #
def _corrupt_files(path):
    return sorted(path.parent.glob(f"{path.name}.corrupt-*"))


@pytest.mark.parametrize("content", ["{niet-json", "", "[]", '"tekst"', "null", "123", '{"camera": '])
def test_corrupt_settings_are_quarantined_not_overwritten(content):
    """Het kapotte bestand werd bij de eerstvolgende config.set() overschreven met
    alleen het verschil met DEFAULTS: alle eigen instellingen waren dan voorgoed weg.
    (Een JSON-array/-string/null liet _deep_merge bovendien crashen -> app start niet.)"""
    import config
    import config.settings as cs

    cs.SETTINGS_FILE.write_text(content, encoding="utf-8")
    config.reload()

    assert config.get("camera.fps") == cs.DEFAULTS["camera"]["fps"]     # defaults actief, geen crash
    found = _corrupt_files(cs.SETTINGS_FILE)
    assert len(found) == 1 and found[0].read_text(encoding="utf-8") == content

    config.set("alarm.time", "07:30")                                      # schrijft een vers bestand
    assert json.loads(cs.SETTINGS_FILE.read_text(encoding="utf-8"))["alarm"]["time"] == "07:30"
    assert found[0].exists() and found[0].read_text(encoding="utf-8") == content


def test_valid_settings_are_not_quarantined():
    import config
    import config.settings as cs

    cs.SETTINGS_FILE.write_text('{"alarm": {"time": "06:00"}}', encoding="utf-8")
    config.reload()
    assert config.get("alarm.time") == "06:00"
    assert _corrupt_files(cs.SETTINGS_FILE) == []


def test_only_the_last_three_corrupt_copies_are_kept(monkeypatch):
    import config
    import config.settings as cs

    stamps = iter(["20260101-000001", "20260101-000002", "20260101-000003", "20260101-000004", "20260101-000005"])
    monkeypatch.setattr(cs.time, "strftime", lambda fmt: next(stamps))
    for i in range(5):
        cs.SETTINGS_FILE.write_text("{kapot %d" % i, encoding="utf-8")
        config.reload()
    kept = _corrupt_files(cs.SETTINGS_FILE)
    assert len(kept) == 3
    assert [p.read_text(encoding="utf-8") for p in kept] == ["{kapot 2", "{kapot 3", "{kapot 4"]
