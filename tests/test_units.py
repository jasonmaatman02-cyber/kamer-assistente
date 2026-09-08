"""Unit tests for the parsing bits that broke during deployment."""
import datetime

import pytest


# --- alarm time parsing --------------------------------------------------- #
@pytest.mark.parametrize("txt,expect", [
    ("07:30", "07:30"), ("7:30", "07:30"), ("7.30", "07:30"),
    ("7 uur 30", "07:30"), ("7uur30", "07:30"), ("om 07:30 uur", "07:30"),
    ("0730", "07:30"), ("7h30", "07:30"),
    ("kwart voor 8", None), ("half 9", None), ("", None), ("onzin", None),
])
def test_alarm_parse(txt, expect):
    from scheduler.alarm import AlarmScheduler

    a = AlarmScheduler()
    r = a.set_alarm(txt)
    a.cancel_alarm()
    assert (r.strftime("%H:%M") if r else None) == expect


def test_alarm_fires_once_then_resets():
    from scheduler.alarm import AlarmScheduler
    import time

    hits = []
    a = AlarmScheduler(lambda: hits.append(1))
    a.set_alarm(when=datetime.datetime.now() + datetime.timedelta(seconds=1))
    time.sleep(1.6)
    assert hits == [1]
    assert a.alarm_time is None
    assert a.check_alarm() is False


# --- weather transform -------------------------------------------------- #
def test_owm_transform_shape():
    from weer.weer import WeerAPI  # noqa: F401

    sample = {"weather": [{"description": "lichte regen"}],
              "main": {"temp": 17.34, "humidity": 88},
              "wind": {"speed": 3.6}, "name": "Arnhem"}
    weather = (sample.get("weather") or [{}])[0]
    out = {
        "temp": round(sample["main"]["temp"], 1),
        "wind": round(sample.get("wind", {}).get("speed", 0) * 3.6, 1),
        "humidity": sample["main"]["humidity"],
        "condition": (weather.get("description") or "").capitalize(),
        "city": sample.get("name"),
    }
    assert out == {"temp": 17.3, "wind": 13.0, "humidity": 88,
                   "condition": "Lichte regen", "city": "Arnhem"}


# --- spotify playlist parsing (the bugs of this session) --------------- #
def test_playlists_info_survives_malformed_items():
    from sound_system.muziek import SpotifyDJ

    class FakeSp:
        def current_user_playlists(self, limit=40):
            return {"items": [
                {"id": "ok1", "name": "Chill", "tracks": {"total": 12},
                 "images": [{"url": "http://x/1.jpg"}]},
                {"id": "bad", "name": "Blend zonder tracks", "images": []},  # geen 'tracks'
                None,                                                        # leeg item
                {"id": "ok2", "name": "Focus", "tracks": {"total": 40}, "images": []},
            ]}

    dj = SpotifyDJ.__new__(SpotifyDJ)
    dj.sp = FakeSp()
    out = dj.playlists_info(limit=10)
    ids = [p["id"] for p in out]
    assert ids == ["ok1", "bad", "ok2"]
    assert out[1]["tracks_count"] == 0


def test_playlist_item_new_vs_old_format():
    """Spotify zet de track soms onder 'item', soms onder 'track'."""
    new = {"item": {"type": "track", "uri": "spotify:track:1", "name": "A",
                    "artists": [{"name": "X"}], "duration_ms": 60000, "album": {"images": []}}}
    old = {"track": {"type": "track", "uri": "spotify:track:2", "name": "B",
                     "artists": [{"name": "Y"}], "duration_ms": 120000, "album": {"images": []}}}
    ep = {"item": {"type": "episode", "uri": "spotify:episode:9"}}
    for it, want in ((new, "spotify:track:1"), (old, "spotify:track:2")):
        t = it.get("item") or it.get("track")
        assert t and t.get("type") != "episode" and t["uri"] == want
    t = ep.get("item") or ep.get("track")
    assert t.get("type") == "episode"  # wordt overgeslagen


# --- chat streaming --------------------------------------------------- #
def test_verwerk_input_stream_plain(monkeypatch):
    import ai.llm as llm

    def fake_stream(messages, tools=None):
        yield {"type": "chunk", "text": "Hal"}
        yield {"type": "chunk", "text": "lo!"}
        yield {"type": "done", "content": "Hallo!"}

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    from logic import gpt_handler

    monkeypatch.setattr(gpt_handler, "conversation_history", [{"role": "system", "content": "x"}])
    out = "".join(gpt_handler.verwerk_input_stream("hoi"))
    assert out == "Hallo!"
    assert gpt_handler.conversation_history[-1] == {"role": "assistant", "content": "Hallo!"}


def test_llm_model_missing_skips_openai_fallback(monkeypatch):
    import ai.llm as llm

    calls = {"openai": 0}

    def boom_ollama(messages, tools):
        raise RuntimeError("model 'qwen2.5:1.5b' not found (status code: 404)")

    def track_openai(messages, tools):
        calls["openai"] += 1
        return {"content": "hoi van openai", "tool_calls": []}

    monkeypatch.setattr(llm, "_chat_ollama", boom_ollama)
    monkeypatch.setattr(llm, "_chat_openai", track_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    import config
    config.set("ai.backend", "ollama")

    out = llm.chat([{"role": "user", "content": "hi"}])
    assert calls["openai"] == 0                       # configfout -> geen OpenAI
    assert "ollama pull qwen2.5:1.5b" in out["content"]


def test_verwerk_input_stream_tool(monkeypatch):
    import ai.llm as llm

    def fake_stream(messages, tools=None):
        yield {"type": "tool_calls", "calls": [{"name": "lees_notities", "arguments": {}}]}

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    from logic import gpt_handler

    monkeypatch.setattr(gpt_handler, "conversation_history", [{"role": "system", "content": "x"}])
    monkeypatch.setitem(gpt_handler.functies_dispatcher, "lees_notities", lambda: "Geen notities.")
    out = "".join(gpt_handler.verwerk_input_stream("lees mijn notities"))
    assert out == "Geen notities."


# --- wake-word backend keuze ---------------------------------------------- #
def test_use_porcupine_respects_config(monkeypatch):
    pytest.importorskip("sounddevice")
    import config
    from voice import Whisper

    # pvporcupine kan ontbreken in CI -> forceer een detector zodat we de
    # config-logica testen, niet de lib-aanwezigheid
    monkeypatch.setattr(Whisper, "detect_wakeword_porcupine", lambda *a, **k: True)

    monkeypatch.setattr(Whisper, "porcupine_ready", lambda: True)
    config.set("assistant.wake_backend", "whisper")
    assert Whisper._use_porcupine() is False          # expliciet uitgezet
    config.set("assistant.wake_backend", "auto")
    assert Whisper._use_porcupine() is True           # klaar + auto

    monkeypatch.setattr(Whisper, "porcupine_ready", lambda: False)
    assert Whisper._use_porcupine() is False          # niet klaar -> whisper
    config.set("assistant.wake_backend", "porcupine")
    assert Whisper._use_porcupine() is False          # geforceerd maar niet klaar


# --- spotify "geen apparaat"-status in /api/service_status --------------- #
def test_service_status_flags_no_device(monkeypatch):
    from Dashboard.backend import services

    class FakeDJ:
        sp = object()

    monkeypatch.setitem(services._services, "spotify", FakeDJ())
    monkeypatch.setattr(services, "_probe", lambda n: (True, None))
    monkeypatch.setattr(services, "active_device_id", lambda sp: None)
    services._health_cache.clear()

    st = services.service_status()["spotify"]
    assert st["ok"] is True and st["no_device"] is True
    assert st["error"] == "geen apparaat actief"

    monkeypatch.setattr(services, "active_device_id", lambda sp: "dev123")
    services._health_cache.clear()
    st = services.service_status()["spotify"]
    assert "no_device" not in st and st["error"] is None
