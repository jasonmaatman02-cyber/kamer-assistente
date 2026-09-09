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

    gpt_handler.reset_session("t1")
    out = "".join(gpt_handler.verwerk_input_stream("hoi", session="t1"))
    assert out == "Hallo!"
    assert gpt_handler.history("t1")[-1] == {"role": "assistant", "content": "Hallo!"}


def test_chat_sessions_are_isolated(monkeypatch):
    import ai.llm as llm
    from logic import gpt_handler

    seen = {}

    def fake_stream(messages, tools=None):
        seen["n"] = len(messages)          # hoeveel berichten ziet de LLM?
        yield {"type": "done", "content": "ok"}

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    gpt_handler.reset_session("a")
    gpt_handler.reset_session("b")
    "".join(gpt_handler.verwerk_input_stream("hoi 1", session="a"))
    "".join(gpt_handler.verwerk_input_stream("hoi 2", session="a"))
    "".join(gpt_handler.verwerk_input_stream("hoi", session="b"))
    assert seen["n"] == 2                  # sessie b: alleen system + 1 user, niet die van a
    assert len(gpt_handler.history("a")) == 5   # system + 2×(user+assistant)


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

    gpt_handler.reset_session("t2")
    monkeypatch.setitem(gpt_handler.functies_dispatcher, "lees_notities", lambda: "Geen notities.")
    out = "".join(gpt_handler.verwerk_input_stream("lees mijn notities", session="t2"))
    assert out == "Geen notities."


def test_stream_tool_then_natural_answer(monkeypatch):
    import ai.llm as llm
    from logic import gpt_handler

    step = {"n": 0}

    def fake_stream(messages, tools=None):
        step["n"] += 1
        if step["n"] == 1:
            yield {"type": "tool_calls", "calls": [{"name": "haal_temp_op", "arguments": {}}]}
        else:                                   # de afronding zonder tools
            assert tools is None
            yield {"type": "chunk", "text": "Het is "}
            yield {"type": "chunk", "text": "12 graden."}
            yield {"type": "done", "content": "Het is 12 graden."}

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setitem(gpt_handler.functies_dispatcher, "haal_temp_op", lambda: "12 graden")
    gpt_handler.reset_session("tp")
    out = "".join(gpt_handler.verwerk_input_stream("hoe warm is het", session="tp"))
    assert out == "Het is 12 graden."
    assert gpt_handler.history("tp")[-1] == {"role": "assistant", "content": "Het is 12 graden."}


def test_tool_failure_is_friendly(monkeypatch):
    from logic import gpt_handler

    def boom(**kw):
        raise RuntimeError("lamp offline")

    monkeypatch.setitem(gpt_handler.functies_dispatcher, "zet_lamp", boom)
    r = gpt_handler._dispatch({"name": "zet_lamp", "arguments": {"aan": True}})
    assert "zet_lamp" in r and "lamp offline" in r and "Traceback" not in r


def test_run_tools_dedupes_identical_calls(monkeypatch):
    from logic import gpt_handler

    hits = []
    monkeypatch.setitem(gpt_handler.functies_dispatcher, "voeg_notitie_toe",
                        lambda inhoud: hits.append(inhoud) or "ok")
    calls = [
        {"name": "voeg_notitie_toe", "arguments": {"inhoud": "melk"}},
        {"name": "voeg_notitie_toe", "arguments": {"inhoud": "melk"}},   # dubbel
        {"name": "voeg_notitie_toe", "arguments": {"inhoud": "brood"}},
    ]
    out = gpt_handler._run_tools(calls)
    assert len(out) == 2 and hits == ["melk", "brood"]


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


# --- services: geen dubbele constructie onder gelijktijdige requests --- #
def test_svc_builds_once_under_concurrency(monkeypatch):
    import threading
    import time as _t

    from Dashboard.backend import services

    services._services.clear()
    builds = []

    def slow_build(name):
        builds.append(name)
        _t.sleep(0.05)
        return object()

    monkeypatch.setattr(services, "_build", slow_build)
    got = []
    threads = [threading.Thread(target=lambda: got.append(services.svc("weer"))) for _ in range(8)]
    for x in threads:
        x.start()
    for x in threads:
        x.join()
    assert builds == ["weer"]                 # precies één keer gebouwd
    assert len({id(g) for g in got}) == 1     # iedereen kreeg hetzelfde object


def test_svc_different_services_build_in_parallel(monkeypatch):
    import threading
    import time as _t

    from Dashboard.backend import services

    services._services.clear()
    services._build_locks.clear()

    def slow_build(name):
        _t.sleep(0.15)
        return object()

    monkeypatch.setattr(services, "_build", slow_build)
    t0 = _t.time()
    threads = [threading.Thread(target=services.svc, args=(n,))
               for n in ("spotify", "radio", "weer", "agenda")]
    for x in threads:
        x.start()
    for x in threads:
        x.join()
    assert _t.time() - t0 < 0.45              # per-naam lock -> 4× parallel, niet serieel


# --- /api/devices: actief (Sonos) apparaat meenemen ------------------- #
def test_devices_includes_currently_playing(client, monkeypatch):
    from Dashboard.backend import services

    class FakeSp:
        def devices(self):
            return {"devices": []}                       # Spotify laat de Sonos weg
        def current_playback(self):
            return {"device": {"id": "sonos1", "name": "Woonkamer",
                               "type": "Speaker", "is_active": True}}

    class FakeDJ:
        sp = FakeSp()

    monkeypatch.setitem(services._services, "spotify", FakeDJ())
    d = client.get("/api/devices").get_json()
    assert d["success"] is True
    assert [x["name"] for x in d["devices"]] == ["Woonkamer"]
    assert d["devices"][0]["active"] is True


# --- camera: kijkers begrenzen ----------------------------------------- #
def test_camera_viewer_cap():
    import config
    from Dashboard.backend.camera_api import _Camera

    config.set("camera.max_viewers", 2)
    cam = _Camera()
    r1 = cam.acquire()
    r2 = cam.acquire()
    assert r1 and r2
    assert cam.acquire() is None             # vol
    r1()                                     # plek terug
    assert cam._viewers == 1
    r1()                                     # idempotent — telt niet dubbel
    assert cam._viewers == 1
    r3 = cam.acquire()
    assert r3 and cam._viewers == 2


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


def test_degrade_only_flags_unexpected(capsys):
    import spotipy

    from Dashboard.backend import services

    services._degrade("x", spotipy.SpotifyException(404, -1, "no device"))
    services._degrade("y", OSError("connection reset"))
    assert capsys.readouterr().out == ""          # verwachte storingen -> stil
    services._degrade("z", KeyError("device"))     # shape-bug -> breadcrumb
    assert "onverwachte fout" in capsys.readouterr().out


def test_weather_data_is_cached(monkeypatch):
    from Dashboard.backend import services

    services._data_cache.clear()
    calls = {"n": 0}

    class FakeW:
        def fetch_weather(self, city=None):
            calls["n"] += 1
            return {"temp": 12, "city": city or "Arnhem"}

    monkeypatch.setitem(services._services, "weer", FakeW())

    a = services.weather_data()
    b = services.weather_data()                  # binnen TTL -> uit cache
    assert a == b and calls["n"] == 1
    services.weather_data(fresh=True)            # bypass
    assert calls["n"] == 2


def test_service_status_probes_run_parallel(monkeypatch):
    import time as _t

    from Dashboard.backend import services

    services._health_cache.clear()
    monkeypatch.setattr(services, "_probe", lambda n: (_t.sleep(0.15) or (True, None)))
    monkeypatch.setattr(services, "_spotify_has_device", lambda: True)
    t0 = _t.time()
    out = services.service_status()
    dt = _t.time() - t0
    assert set(out) == {"spotify", "radio", "weer", "agenda"}
    assert dt < 0.45, f"probes lijken serieel ({dt:.2f}s, verwacht ~0.15s)"

    monkeypatch.setattr(services, "active_device_id", lambda sp: "dev123")
    services._health_cache.clear()
    st = services.service_status()["spotify"]
    assert "no_device" not in st and st["error"] is None
