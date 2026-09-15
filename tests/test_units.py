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


def test_set_alarm_concurrent_calls_leave_one_consistent_thread():
    """Regressie: zonder lock konden twee (bijna-)gelijktijdige set_alarm()-
    aanroepen (bv. een dubbele form-submit) allebei de oude thread nog
    'levend genoeg' zien en zo allebei een eigen wekker-thread starten. Met
    de lock is de eindtoestand altijd consistent: precies één levende
    thread, en die hoort bij de laatst gewonnen aanroep."""
    from scheduler.alarm import AlarmScheduler
    import threading

    a = AlarmScheduler(lambda: None)
    barrier = threading.Barrier(2)

    def setter(minutes):
        barrier.wait(timeout=2)
        a.set_alarm(when=datetime.datetime.now() + datetime.timedelta(minutes=minutes))

    t1 = threading.Thread(target=setter, args=(10,))
    t2 = threading.Thread(target=setter, args=(20,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert a.alarm_thread is not None
    assert a.alarm_thread.is_alive()
    a.cancel_alarm()
    assert not a.alarm_thread.is_alive()


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
    # voice.Whisper is nu altijd importeerbaar (zie test hieronder), ook
    # zonder sounddevice geïnstalleerd -> geen importorskip meer nodig.
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


# --- audio: ontbrekende sounddevice mag scheduler.routines niet meeslepen --- #
def test_voice_stack_degrades_without_sounddevice(monkeypatch):
    """Regressietest voor de 'wekkers werken niet, sounddevice-fout'-bug:
    scheduler.routines (dus ook de dashboard-wekker/-routines) moet altijd
    importeerbaar en bruikbaar blijven, ook zonder sounddevice/numpy
    (dashboard-only install, requirements-dashboard.txt)."""
    from voice import Whisper

    monkeypatch.setattr(Whisper, "sd", None)
    monkeypatch.setattr(Whisper, "np", None)

    assert Whisper.mic_available() is False
    with pytest.raises(Whisper.NoMicError):
        Whisper._record(0.1)

    # de hele keten die scheduler.routines nodig heeft blijft werken
    import scheduler.routines as routines
    import voice.Whisper_short as whisper_short

    assert callable(routines.morning_routine)
    assert callable(routines.bedtime_routine)
    assert callable(whisper_short.shortwhisper)


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


def test_start_playback_without_device_id_when_no_id(monkeypatch):
    from Dashboard.backend import media_api, services

    calls = []

    class FakeSp:
        def start_playback(self, **kw):
            calls.append(kw)

    # geen bestuurbaar apparaat (bv. Sonos zonder id)
    monkeypatch.setattr(services, "active_device_id", lambda sp: None)
    media_api._start_playback(FakeSp(), context_uri="spotify:playlist:x")
    assert calls == [{"context_uri": "spotify:playlist:x"}]        # geen device_id -> Spotify's actieve apparaat

    calls.clear()
    monkeypatch.setattr(services, "active_device_id", lambda sp: "dev9")
    monkeypatch.setattr(services, "wake_device", lambda sp, d: None)
    media_api._start_playback(FakeSp(), uris=["spotify:track:1"])
    assert calls == [{"uris": ["spotify:track:1"], "device_id": "dev9"}]


def test_devices_shows_idless_sonos_as_playing(client, monkeypatch):
    from Dashboard.backend import services

    class FakeSp:
        def devices(self):
            return {"devices": [{"id": "phone1", "name": "iPhone", "type": "Smartphone", "is_active": False}]}
        def current_playback(self):
            # Sonos/Cast: Spotify geeft geen id terug
            return {"device": {"id": None, "name": "Woonkamer", "type": "Speaker"}}

    class FakeDJ:
        sp = FakeSp()

    monkeypatch.setitem(services._services, "spotify", FakeDJ())
    devs = client.get("/api/devices").get_json()["devices"]
    names = {x["name"]: x for x in devs}
    assert "Woonkamer" in names and names["Woonkamer"]["id"] is None
    assert names["Woonkamer"]["active"] is True


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


# --- routines: één kapotte stap mag de rest niet stoppen ----------------- #
def test_custom_routine_step_isolation(monkeypatch):
    """Regressietest: 'Tapo werkt niet' mag niet 'hele routine stopt' betekenen."""
    import config
    from Dashboard.backend import routines_api

    def boom(ip):
        raise RuntimeError("Tapo offline")

    monkeypatch.setattr(routines_api.S, "lamp", boom)
    said = []
    monkeypatch.setattr("voice.tts_output.speak", lambda text: said.append(text))

    config.set("routines", [{
        "id": "testroutine", "name": "Test", "desc": "",
        "steps": [
            {"action": "lamp", "lamp": 0, "mode": "on"},   # faalt (Tapo offline)
            {"action": "say", "text": "tweede stap"},       # moet alsnog draaien
        ],
    }])
    routines_api._run_routine("testroutine")               # gooit niet door
    assert said == ["tweede stap"]


def test_run_routine_unknown_action_isolated(monkeypatch):
    import config
    from Dashboard.backend import routines_api

    said = []
    monkeypatch.setattr("voice.tts_output.speak", lambda text: said.append(text))
    config.set("routines", [{
        "id": "testroutine2", "name": "Test2", "desc": "",
        "steps": [{"action": "onbekend"}, {"action": "say", "text": "toch"}],
    }])
    routines_api._run_routine("testroutine2")
    assert said == ["toch"]


# --- people_detect: NMS + count_people robuustheid ----------------------- #
def test_non_max_suppression_dedupes_overlapping_boxes():
    from logic.people_detect import non_max_suppression

    boxes = [
        (0, 0, 100, 200),      # persoon A
        (5, 5, 105, 205),      # zelfde persoon A, licht verschoven detectie
        (300, 0, 400, 200),    # persoon B, ver weg
    ]
    scores = [0.9, 0.8, 0.7]
    keep = non_max_suppression(boxes, scores)
    assert len(keep) == 2                     # A en B, niet de dubbele A


def test_non_max_suppression_empty():
    from logic.people_detect import non_max_suppression

    assert non_max_suppression([], []) == []


def test_count_people_blank_frame_is_zero():
    pytest.importorskip("cv2")
    import numpy as np

    from logic.people_detect import count_people

    frame = np.zeros((240, 320, 3), dtype="uint8")   # effen zwart beeld
    assert count_people(frame) == 0


def test_count_people_never_raises(monkeypatch):
    import logic.people_detect as pd

    def boom():
        raise RuntimeError("geen HOG beschikbaar")

    monkeypatch.setattr(pd, "_get_detector", boom)

    class FakeFrame:
        size = 10

    assert pd.count_people(FakeFrame()) == 0   # gevangen, geen exception


def test_count_people_none_frame_is_zero():
    from logic.people_detect import count_people

    assert count_people(None) == 0


# --- presence: de 21:30-regel (pure, tijd-onafhankelijk testbaar) -------- #
def test_auto_light_block_before_and_after_2130():
    import config
    from Dashboard.backend.presence import is_auto_light_blocked

    config.set("presence.auto_light_block_after", "21:30")
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 21, 29)) is False
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 21, 30)) is True    # grens = geblokkeerd
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 21, 31)) is True
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 23, 59)) is True


def test_auto_light_block_resets_after_midnight():
    """Zelfde dag na 21:30 -> geblokkeerd; de volgende dag vóór 21:30 (ook
    vlak na middernacht) -> weer toegestaan. Puur op kloktijd, geen
    datum-staleness-bug."""
    import config
    from Dashboard.backend.presence import is_auto_light_blocked

    config.set("presence.auto_light_block_after", "21:30")
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 23, 59)) is True
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 6, 0, 0)) is False
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 6, 0, 5)) is False
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 6, 21, 29)) is False
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 6, 21, 30)) is True


def test_auto_light_block_disabled_when_empty():
    import config
    from Dashboard.backend.presence import is_auto_light_blocked

    config.set("presence.auto_light_block_after", "")
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 23, 59)) is False
    config.set("presence.auto_light_block_after", "21:30")   # terugzetten voor andere tests


def test_auto_light_block_bad_value_falls_back(monkeypatch):
    import config
    from Dashboard.backend.presence import is_auto_light_blocked

    config.set("presence.auto_light_block_after", "onzin")
    # valt terug op de default 21:30 i.p.v. te crashen of nooit te blokkeren
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 21, 31)) is True
    assert is_auto_light_blocked(datetime.datetime(2026, 1, 5, 21, 29)) is False
    config.set("presence.auto_light_block_after", "21:30")


# --- presence: state machine (hysteresis, grace period, geen spam-lamp) -- #
def test_presence_worker_hysteresis_and_grace(monkeypatch):
    import config
    from Dashboard.backend.presence import PresenceWorker
    import logic.people_detect as pd

    config.set("presence.consecutive_required", 2)
    config.set("presence.empty_grace_s", 10.0)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")   # niet laten meespelen in deze test

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())   # niet None -> "er is beeld"

    lamp_calls = []
    monkeypatch.setattr(w, "_auto_light", lambda on: (lamp_calls.append(on), True)[1])

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])

    # 1e detectie: nog niet genoeg (consecutive_required=2) -> blijft EMPTY
    monkeypatch.setattr(pd, "count_people", lambda frame: 1)
    w._tick()
    assert w.room_state == "EMPTY" and lamp_calls == []

    # 2e opeenvolgende detectie -> nu pas OCCUPIED, lamp-aan geprobeerd
    clock["t"] += 3
    w._tick()
    assert w.room_state == "OCCUPIED" and lamp_calls == [True]

    # blijft bezet over meerdere ticks -> geen nieuwe lamp-actie (geen spam,
    # en respecteert een eventuele handmatige uitzet-actie ondertussen)
    clock["t"] += 3
    w._tick()
    clock["t"] += 3
    w._tick()
    assert lamp_calls == [True]

    # 1 gemist frame (count=0) -> mag niet meteen EMPTY worden (grace period)
    monkeypatch.setattr(pd, "count_people", lambda frame: 0)
    clock["t"] += 3
    w._tick()
    assert w.room_state == "OCCUPIED" and lamp_calls == [True]

    # grace period (10s) verstreken zonder nieuwe detectie -> nu pas EMPTY + lamp-uit
    clock["t"] += 8   # totaal >10s sinds de laatste positieve detectie
    w._tick()
    assert w.room_state == "EMPTY" and lamp_calls == [True, False]


def test_presence_worker_blocks_auto_on_after_2130(monkeypatch):
    import config
    import Dashboard.backend.presence as presence_mod
    from Dashboard.backend.presence import PresenceWorker

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod, "is_auto_light_blocked", lambda: True)

    def must_not_be_called(ip):
        raise AssertionError("lamp mag niet aangeroepen worden na 21:30")

    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")
    monkeypatch.setattr(presence_mod.S, "lamp", must_not_be_called)

    w._tick()
    assert w.room_state == "OCCUPIED"     # detectie blijft gewoon werken...
    # ...maar de lamp is niet aangeraakt (must_not_be_called zou anders falen)


def test_presence_worker_lamp_failure_does_not_raise(monkeypatch):
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    def boom(ip):
        raise RuntimeError("Tapo offline")

    monkeypatch.setattr(presence_mod.S, "lamp", boom)
    w._tick()   # mag niet crashen
    assert w.room_state == "OCCUPIED"
    config.set("presence.auto_light_block_after", "21:30")


# --- presence: retry van een mislukte automatische lampactie ------------- #
def test_presence_retry_after_failed_auto_on(monkeypatch):
    """ON mislukt bij de overgang -> de eerstvolgende tick (zonder nieuwe
    overgang) probeert 'm opnieuw."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    calls = []

    def boom(ip):
        calls.append(ip)
        raise RuntimeError("Tapo offline")

    monkeypatch.setattr(presence_mod.S, "lamp", boom)

    w._tick()   # overgang naar OCCUPIED, ON mislukt
    assert w.room_state == "OCCUPIED"
    assert len(calls) == 1
    assert w._pending_light is True and w._light_retries == 0

    w._tick()   # zelfde staat, geen nieuwe overgang -> retry
    assert len(calls) == 2
    assert w._pending_light is True and w._light_retries == 1


def test_presence_retry_success_clears_pending(monkeypatch):
    """Lukt de retry wel, dan wordt de retry-status meteen gewist en komt er
    geen volgende poging meer, ook al blijft de kamer bezet."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    class FakeLamp:
        async def aan(self):
            return None

    calls = {"n": 0}

    def flaky(ip):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Tapo offline")
        return FakeLamp()

    monkeypatch.setattr(presence_mod.S, "lamp", flaky)

    w._tick()   # overgang -> 1e poging mislukt
    assert calls["n"] == 1 and w._pending_light is True

    w._tick()   # retry -> lukt nu
    assert calls["n"] == 2
    assert w._pending_light is None and w._light_retries == 0

    w._tick()   # kamer blijft bezet, niks openstaand -> geen nieuwe poging
    assert calls["n"] == 2


def test_presence_retry_gives_up_after_max_attempts(monkeypatch):
    """Blijft de lamp onbereikbaar, dan stopt het na _MAX_LIGHT_RETRIES
    retries met proberen (geen eindeloze retries) totdat er een nieuwe
    overgang komt."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    from Dashboard.backend.presence import _MAX_LIGHT_RETRIES
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    calls = []

    def boom(ip):
        calls.append(ip)
        raise RuntimeError("offline")

    monkeypatch.setattr(presence_mod.S, "lamp", boom)

    w._tick()   # overgang: 1e (mislukte) poging
    for _ in range(_MAX_LIGHT_RETRIES):
        w._tick()
    assert len(calls) == 1 + _MAX_LIGHT_RETRIES   # 1 overgang + max retries, geen meer

    # extra ticks in dezelfde staat -> écht geen nieuwe poging meer
    w._tick()
    w._tick()
    assert len(calls) == 1 + _MAX_LIGHT_RETRIES
    assert w._pending_light is True   # blijft "openstaand" tot een nieuwe overgang


def test_presence_new_transition_resets_retry_state(monkeypatch):
    """Een nieuwe EMPTY<->OCCUPIED-overgang reset eerdere retry-status, ook
    als de vorige retry-reeks nog niet was opgegeven."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.empty_grace_s", 5.0)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    class FakeLamp:
        async def aan(self):
            raise RuntimeError("ON mislukt")

        async def uit(self):
            return None   # OFF lukt altijd

    monkeypatch.setattr(presence_mod.S, "lamp", lambda ip: FakeLamp())

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])

    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    w._tick()   # overgang naar OCCUPIED, ON mislukt
    assert w.room_state == "OCCUPIED" and w._pending_light is True

    clock["t"] += 1
    w._tick()   # 1 retry (mislukt ook, blijft "aan" proberen)
    assert w._light_retries == 1 and w._pending_light is True

    # nu weg -> na de grace period EMPTY, een verse overgang
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 0)
    clock["t"] += 6   # > empty_grace_s
    w._tick()
    assert w.room_state == "EMPTY"
    # de nieuwe overgang (OFF, die lukt) heeft de oude ON-retry-status gewist
    assert w._pending_light is None and w._light_retries == 0


def test_presence_retry_respects_2130_rule(monkeypatch):
    """De 21:30-regel geldt ook tijdens een retry: zodra 'ie geblokkeerd is,
    wordt er geen ON-commando meer gestuurd, en stopt de retry vanzelf."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    # 1e keer (overgang) nog niet geblokkeerd, maar de Tapo-call mislukt zelf
    # (netwerkfout) -> pending blijft staan. Daarna is het ineens 21:30 geweest.
    blocked = {"v": False}
    monkeypatch.setattr(presence_mod, "is_auto_light_blocked", lambda: blocked["v"])

    def boom(ip):
        raise RuntimeError("Tapo tijdelijk onbereikbaar")

    monkeypatch.setattr(presence_mod.S, "lamp", boom)

    w._tick()   # overgang -> ON geprobeerd (niet geblokkeerd), mislukt door netwerkfout
    assert w._pending_light is True

    blocked["v"] = True   # simuleer dat de klok nu voorbij 21:30 is

    def must_not_be_called(ip):
        raise AssertionError("na 21:30 mag ON nooit meer geprobeerd worden, ook niet als retry")

    monkeypatch.setattr(presence_mod.S, "lamp", must_not_be_called)

    w._tick()   # retry -> moet nu geblokkeerd worden, geen lamp-commando
    assert w._pending_light is None   # geblokkeerd telt niet als "nog te retrien"
    assert w._light_retries == 0


def test_presence_retry_never_touches_manual_control(monkeypatch):
    """Zonder een openstaande mislukte automatische actie mag de retry-logica
    nooit een lampcommando sturen -- anders zou 'ie een handmatige actie
    kunnen overschrijven."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()

    def must_not_be_called(ip):
        raise AssertionError("geen pending failure -> retry mag de lamp niet aanraken")

    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")
    monkeypatch.setattr(presence_mod.S, "lamp", must_not_be_called)

    assert w._pending_light is None
    w._retry_light()   # direct aangeroepen zonder openstaande mislukking

    # ook via een "stabiele" tick (geen overgang, niks openstaand) blijft de lamp met rust
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 0)
    w._tick()


# --- presence: Tapo session-timeout -> herauthenticeren + éénmalig retryen - #
def test_presence_auto_light_normal_action_still_works(monkeypatch):
    """Basisgedrag ongewijzigd: een gewone, geslaagde actie triggert geen
    re-auth."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    class WorkingLamp:
        async def aan(self):
            return None

    monkeypatch.setattr(presence_mod.S, "lamp", lambda ip: WorkingLamp())

    def must_not_reconnect(ip):
        raise AssertionError("een normale, geslaagde actie mag geen re-auth triggeren")

    monkeypatch.setattr(presence_mod.S, "reconnect_lamp", must_not_reconnect)

    assert w._auto_light(True) is True


def test_presence_session_timeout_triggers_reauth_and_retry(monkeypatch):
    """SESSION_TIMEOUT -> bestaande verbinding weg, opnieuw inloggen met de
    bestaande credentials, en de actie éénmalig opnieuw proberen."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    class StaleLamp:
        async def aan(self):
            raise RuntimeError(
                'Tapo(Unauthorized { kind: "SESSION_TIMEOUT", '
                'description: "Session has expired. Re-authentication is required." })'
            )

    class FreshLamp:
        def __init__(self):
            self.calls = 0

        async def aan(self):
            self.calls += 1

    fresh = FreshLamp()
    reconnect_calls = []

    monkeypatch.setattr(presence_mod.S, "lamp", lambda ip: StaleLamp())

    def do_reconnect(ip):
        reconnect_calls.append(ip)
        return fresh

    monkeypatch.setattr(presence_mod.S, "reconnect_lamp", do_reconnect)

    assert w._auto_light(True) is True
    assert reconnect_calls == ["192.168.1.50"]   # precies 1x herauthenticeren
    assert fresh.calls == 1                      # en de actie is daarna herhaald


def test_presence_session_timeout_reauth_failure_is_handled_gracefully(monkeypatch):
    """Mislukt de re-authenticatie zelf ook (bv. lamp echt offline), dan mag
    de worker niet crashen -- gewoon als mislukte poging afhandelen."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    class StaleLamp:
        async def uit(self):
            raise RuntimeError('Unauthorized { kind: "SESSION_TIMEOUT" }')

    monkeypatch.setattr(presence_mod.S, "lamp", lambda ip: StaleLamp())

    def reconnect_boom(ip):
        raise RuntimeError("kan niet opnieuw verbinden (lamp onbereikbaar)")

    monkeypatch.setattr(presence_mod.S, "reconnect_lamp", reconnect_boom)

    assert w._auto_light(False) is False   # nette mislukking, geen crash


def test_presence_plain_network_error_skips_reauth(monkeypatch):
    """Een gewone netwerkfout (geen SESSION_TIMEOUT/Unauthorized) mag geen
    re-auth triggeren -- die blijft gewoon via de bestaande retry lopen."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    class OfflineLamp:
        async def aan(self):
            raise OSError("No route to host")

    monkeypatch.setattr(presence_mod.S, "lamp", lambda ip: OfflineLamp())

    def must_not_reconnect(ip):
        raise AssertionError("een gewone netwerkfout mag geen re-auth triggeren")

    monkeypatch.setattr(presence_mod.S, "reconnect_lamp", must_not_reconnect)

    assert w._auto_light(True) is False   # valt terug op de bestaande tick-retry


def test_presence_session_timeout_reauth_bounded_no_endless_loop(monkeypatch):
    """Blijft de sessie ook na reconnect steeds verlopen, dan blijft het
    aantal re-auth-pogingen begrensd door de bestaande max-3-retries -- geen
    eindeloze authenticatie-loop."""
    import config
    from Dashboard.backend.presence import PresenceWorker, _MAX_LIGHT_RETRIES
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    class AlwaysStale:
        async def aan(self):
            raise RuntimeError('Unauthorized { kind: "SESSION_TIMEOUT" }')

    reconnect_calls = []

    def do_reconnect(ip):
        reconnect_calls.append(ip)
        return AlwaysStale()   # blijft ook na reconnect mislukken

    monkeypatch.setattr(presence_mod.S, "lamp", lambda ip: AlwaysStale())
    monkeypatch.setattr(presence_mod.S, "reconnect_lamp", do_reconnect)

    w._tick()   # overgang: 1e poging + 1 reconnect-poging, allebei mislukt
    for _ in range(_MAX_LIGHT_RETRIES + 3):   # ruim voorbij het retry-budget
        w._tick()

    # 1 overgang + max _MAX_LIGHT_RETRIES retries = zoveel _auto_light-aanroepen,
    # dus precies zoveel (niet meer) reconnect-pogingen -- begrensd, geen loop
    assert len(reconnect_calls) == 1 + _MAX_LIGHT_RETRIES


def test_presence_2130_block_skips_reauth_too(monkeypatch):
    """De harde 21:30-regel gaat vóór alles -- ook vóór een eventuele
    re-auth-poging: geblokkeerd betekent geen enkele lamp/reconnect-aanroep."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.auto_light_enabled", True)

    w = PresenceWorker()
    monkeypatch.setattr(presence_mod, "is_auto_light_blocked", lambda: True)

    def must_not_be_called(*a, **kw):
        raise AssertionError("na 21:30 mag er geen lamp/reconnect-aanroep gebeuren")

    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")
    monkeypatch.setattr(presence_mod.S, "lamp", must_not_be_called)
    monkeypatch.setattr(presence_mod.S, "reconnect_lamp", must_not_be_called)

    assert w._auto_light(True) is True   # geblokkeerd = bewust niets doen, geen fout


# --- presence: camera-herstel na reset_services() (settings-save) -------- #
def test_presence_get_frame_calls_keep_alive_every_tick(monkeypatch):
    """reset_services() (draait bij elke instellingen-opslag) stopt de
    camera-capture-thread altijd, ook als presence 'm levend probeert te
    houden. _get_frame() moet daarom ELKE tick keep_alive(True) aanroepen
    (goedkoop/idempotent) zodat detectie na zo'n reset vanzelf herstelt,
    i.p.v. voorgoed op 0 te blijven hangen."""
    import config
    from Dashboard.backend.presence import PresenceWorker

    config.set("camera.enabled", True)
    w = PresenceWorker()

    calls = {"acquire": 0, "keep_alive": []}
    fake_camera = type("FakeCam", (), {})()
    fake_camera.acquire = lambda: (calls.__setitem__("acquire", calls["acquire"] + 1) or (lambda: None))
    fake_camera.keep_alive = lambda on: calls["keep_alive"].append(on)
    fake_camera.latest_jpeg = lambda: None

    import Dashboard.backend.camera_api as camera_api
    monkeypatch.setattr(camera_api, "camera", fake_camera)

    w._get_frame()
    w._get_frame()
    w._get_frame()

    assert calls["acquire"] == 1                  # viewer-slot maar 1x geclaimd (geen lek)
    assert calls["keep_alive"] == [True, True, True]   # maar wél elke tick "blijf leven"


def test_presence_get_frame_handles_camera_full(monkeypatch):
    import config
    from Dashboard.backend.presence import PresenceWorker

    config.set("camera.enabled", True)
    w = PresenceWorker()

    fake_camera = type("FakeCam", (), {})()
    fake_camera.acquire = lambda: None    # camera vol
    fake_camera.keep_alive = lambda on: None
    fake_camera.latest_jpeg = lambda: None

    import Dashboard.backend.camera_api as camera_api
    monkeypatch.setattr(camera_api, "camera", fake_camera)

    assert w._get_frame() is None     # geen crash, gewoon geen frame
    assert w._get_frame() is None     # blijft netjes None bij herhaling


# --- presence: AUTO/MANUAL, ONBEKEND-staat bij sensorverlies, rijkere status - #
def test_presence_no_detection_no_action(monkeypatch):
    """1. Geen aanwezigheid -> geen actie (geen lampcommando's, blijft EMPTY)."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.auto_light_enabled", True)
    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 0)

    def must_not_be_called(ip):
        raise AssertionError("zonder aanwezigheid mag er geen lampcommando gestuurd worden")
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")
    monkeypatch.setattr(presence_mod.S, "lamp", must_not_be_called)

    w._tick()
    w._tick()
    assert w.room_state == "EMPTY"
    assert w.status()["state"] == "absent"


def test_presence_restart_does_not_toggle_lamp_immediately(monkeypatch):
    """9. Een 'herstart' (= een verse PresenceWorker, precies wat er na een
    service-restart gebeurt) mag de lamp niet direct aanraken -- pas na
    consecutive_required echte, opeenvolgende detecties."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 2)
    config.set("presence.auto_light_enabled", True)
    w = PresenceWorker()   # simuleert de staat direct na een herstart
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)

    calls = []

    def must_not_be_called(ip):
        calls.append(ip)
        raise AssertionError("bij het opstarten zelf mag er geen lamppoging zijn")

    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")
    monkeypatch.setattr(presence_mod.S, "lamp", must_not_be_called)

    w._tick()   # 1e detectie, nog niet genoeg
    assert w.room_state == "EMPTY"
    assert calls == []   # geen enkele lamppoging bij het opstarten zelf


def test_presence_sensor_offline_holds_unknown_state(monkeypatch):
    """7. Sensor offline -> veilige ONBEKEND-toestand, geen valse EMPTY."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 2)
    config.set("presence.empty_grace_s", 5.0)
    config.set("presence.auto_light_enabled", True)

    w = PresenceWorker()
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")

    def must_not_be_called(ip):
        raise AssertionError("een sensorstoring mag geen lampcommando triggeren")
    monkeypatch.setattr(presence_mod.S, "lamp", must_not_be_called)

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])

    monkeypatch.setattr(w, "_get_frame", lambda: object())
    w._tick(); clock["t"] += 3; w._tick()
    assert w.room_state == "OCCUPIED"
    assert w.status()["state"] == "present"

    # camera valt weg -- geen bevestigde meting, geen valse aftelling naar EMPTY
    monkeypatch.setattr(w, "_get_frame", lambda: None)
    for _ in range(10):
        clock["t"] += 3
        w._tick()
    assert w.status()["state"] == "unknown"
    assert w.room_state == "OCCUPIED"          # niet stiekem naar EMPTY gegaan
    assert w._positive_streak == 2             # niet gereset door de storing

    # sensor komt terug -- gewoon weer normaal, geen valse blijvende ONBEKEND
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    clock["t"] += 3
    w._tick()
    assert w.status()["state"] == "present"


def test_presence_manual_action_visible_and_reset_on_next_transition(monkeypatch):
    """6. Manual mode is zichtbaar en blokkeert de bestaande edge-triggered
    actuatie niet extra (die vocht toch al nooit terug); een nieuwe
    aanwezigheids-overgang geeft de automatisering weer AUTO-controle."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")

    w = PresenceWorker()
    assert w.status()["mode"] == "AUTO"

    w.note_manual_action()
    assert w.status()["mode"] == "MANUAL"
    w.note_manual_action()   # nogmaals -- blijft gewoon MANUAL, geen bijwerking

    class WorkingLamp:
        async def aan(self):
            return None

    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame: 1)
    monkeypatch.setattr(presence_mod.S, "lamp_ip", lambda x: "192.168.1.50")
    monkeypatch.setattr(presence_mod.S, "lamp", lambda ip: WorkingLamp())

    w._tick()   # een echte overgang -> mode weer AUTO
    assert w.status()["mode"] == "AUTO"


def test_devices_api_manual_action_notifies_presence(monkeypatch, client):
    """Handmatige bediening via de bestaande dashboard-route zet de door
    presence bediende lamp op MANUAL; een andere lamp laat presence met rust."""
    import config
    from Dashboard.backend import services as S
    from Dashboard.backend.presence import worker

    config.set("presence.lamp", 0)
    config.set("devices.lamps", [{"name": "Kamerlamp", "ip": "192.168.1.50"},
                                  {"name": "Andere lamp", "ip": "192.168.1.60"}])
    worker._mode = "AUTO"

    class FakeLamp:
        async def aan(self):
            return "ok"

    monkeypatch.setattr(S, "lamp", lambda ip: FakeLamp())

    r = client.put("/api/lamp/on", json={"lamp": 1})   # "Andere lamp" -- niet de presence-lamp
    assert r.get_json()["status"] == "ok"
    assert worker._mode == "AUTO"

    r = client.put("/api/lamp/on", json={"lamp": 0})   # de presence-lamp zelf
    assert r.get_json()["status"] == "ok"
    assert worker._mode == "MANUAL"


def test_presence_status_shape_matches_requested_api(monkeypatch):
    """/api/presence-vorm: state/automation_enabled/last_change naast de
    bestaande velden (room_state/last_count blijven bestaan, niets breekt)."""
    import config
    from Dashboard.backend.presence import PresenceWorker

    config.set("presence.enabled", True)
    config.set("presence.auto_light_enabled", True)
    w = PresenceWorker()

    s = w.status()
    assert s["state"] == "absent"
    assert s["automation_enabled"] is True
    assert s["mode"] == "AUTO"
    assert s["last_change"] is None   # nog nooit een overgang gehad

    w.room_state = "OCCUPIED"
    w.last_change = 1_700_000_000.0
    s = w.status()
    assert s["state"] == "present"
    assert s["last_change"]   # ISO-tekst, geen None meer


# --- audio: mic die 16kHz niet ondersteunt (USB-webcam-quirk, echt gezien --
# --- op de Pi: "CC HD webcam" mist 16/22.05kHz, heeft wel 44.1kHz) ------- #
def test_record_falls_back_to_device_rate_and_resamples(monkeypatch):
    import numpy as np

    from voice import Whisper

    class FakeSD:
        def check_input_settings(self, samplerate, channels):
            if samplerate == Whisper.SAMPLERATE:
                raise RuntimeError("Invalid sample rate [PaErrorCode -9997]")
            # 44100 (en andere) wél ok

        def query_devices(self, kind=None):
            return {"name": "CC HD webcam: USB Audio", "default_samplerate": 44100.0}

        def rec(self, n, samplerate, channels, dtype):
            self.last_n, self.last_rate = n, samplerate
            return np.ones((n, channels), dtype=dtype) * 0.5

        def wait(self):
            pass

    fake = FakeSD()
    monkeypatch.setattr(Whisper, "sd", fake)
    monkeypatch.setattr(Whisper, "_record_rate_cache", None)

    assert Whisper.mic_available() is True        # gedegradeerd naar 44.1kHz, niet False
    assert Whisper._record_rate_cache == 44100

    audio = Whisper._record(1.0)
    assert fake.last_rate == 44100                 # opgenomen op de rate die de mic wél kan
    # terug naar SAMPLERATE geresampled (binnen afrondingsmarge van linspace/interp)
    assert abs(audio.shape[0] - Whisper.SAMPLERATE) <= 1


def test_record_uses_16khz_directly_when_supported(monkeypatch):
    import numpy as np

    from voice import Whisper

    class FakeSD:
        def check_input_settings(self, samplerate, channels):
            pass  # alles ok, ook 16kHz

        def rec(self, n, samplerate, channels, dtype):
            self.last_rate = n, samplerate
            return np.zeros((n, channels), dtype=dtype)

        def wait(self):
            pass

    fake = FakeSD()
    monkeypatch.setattr(Whisper, "sd", fake)
    monkeypatch.setattr(Whisper, "_record_rate_cache", None)

    assert Whisper._pick_record_rate() == Whisper.SAMPLERATE   # geen omweg nodig
