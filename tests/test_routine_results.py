"""Routines melden eerlijk wat er niet lukte (geen 'Routine uitgevoerd' terwijl de lamp niet reageerde)."""
import pytest

import config
from Dashboard.backend import services as S


class _BoomLamp:
    async def party(self):
        raise RuntimeError("lamp onbereikbaar")

    async def bureau(self):
        raise RuntimeError("lamp onbereikbaar")

    async def aan(self):
        raise RuntimeError("lamp onbereikbaar")


class _OkLamp:
    async def bureau(self):
        return "ok"

    async def aan(self):
        return "ok"

    async def uit(self):
        return "ok"

    async def party(self):
        return "ok"

    async def normaal(self):
        return "ok"


@pytest.fixture()
def lamp(monkeypatch):
    holder = {"lamp": _BoomLamp()}
    config.set("devices.lamps", [{"name": "A", "ip": "192.0.2.1"}])
    monkeypatch.setattr(S, "lamp", lambda ip: holder["lamp"])
    return holder


def test_single_action_routine_with_a_failed_lamp_is_not_reported_as_success(client, lamp):
    r = client.post("/api/routines/run", json={"id": "desk"})
    assert r.status_code == 502
    body = r.get_json()
    assert body["success"] is False and "lamp onbereikbaar" in body["error"]


def test_single_action_routine_success(client, lamp):
    lamp["lamp"] = _OkLamp()
    r = client.post("/api/routines/run", json={"id": "desk"})
    assert r.status_code == 200 and r.get_json() == {"success": True}


def _custom(steps):
    config.set("routines", [{"id": "mijn", "name": "Mijn", "desc": "", "steps": steps}])


def test_custom_routine_partial_failure_is_success_with_warnings(client, lamp, monkeypatch):
    lamp["lamp"] = _OkLamp()
    _custom([{"action": "lamp", "lamp": 0, "mode": "desk"}, {"action": "nope"}])
    r = client.post("/api/routines/run", json={"id": "mijn"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True and len(body["warnings"]) == 1
    assert "onbekende actie" in body["warnings"][0] and "Stap 2" in body["warnings"][0]


def test_custom_routine_where_every_step_fails_is_a_failure(client, lamp):
    _custom([{"action": "lamp", "lamp": 0, "mode": "on"}, {"action": "nope"}])
    r = client.post("/api/routines/run", json={"id": "mijn"})
    assert r.status_code == 502
    assert r.get_json()["success"] is False


def test_custom_routine_with_garbage_steps_does_not_crash(client, lamp):
    _custom([1, None, "x", {"action": 5}])
    r = client.post("/api/routines/run", json={"id": "mijn"})
    assert r.status_code == 502            # niets gelukt, maar wel een nette JSON-fout
    assert r.is_json


def test_spotify_step_without_spotify_fails_loudly(client):
    """Stilzwijgend niets doen (dj is None) gold als 'gelukt'."""
    _custom([{"action": "spotify", "playlist_id": "abc"}])
    r = client.post("/api/routines/run", json={"id": "mijn"})
    assert r.status_code == 502
    assert "Spotify" in r.get_json()["error"]


def test_spotify_step_uses_the_shared_default_device_logic(client, monkeypatch):
    calls = []

    class DJ:
        sp = object()

    monkeypatch.setattr(S, "sp_dj", lambda: DJ())
    monkeypatch.setattr(S, "start_playback", lambda sp, **kw: calls.append((sp, kw)))
    _custom([{"action": "spotify", "playlist_id": "abc"}])
    assert client.post("/api/routines/run", json={"id": "mijn"}).status_code == 200
    assert calls == [(DJ.sp, {"context_uri": "spotify:playlist:abc"})]


# --------------------------------------------------------------------------- #
# scheduler.routines: lijst met mislukte stappen
# --------------------------------------------------------------------------- #
def test_morning_routine_returns_failed_steps(monkeypatch):
    import scheduler.routines as R

    monkeypatch.setattr(R, "speak", lambda text: None)
    from ai import llm
    monkeypatch.setattr(llm, "complete", lambda p, **kw: "hoi")

    class BoomRadio:
        def play(self, name):
            raise RuntimeError("geen internet")

    monkeypatch.setattr(R, "RadioPlayer", BoomRadio)
    config.set("features.radio", True)
    failures = R.morning_routine()
    assert len(failures) == 1 and failures[0].startswith("Radio:") and "geen internet" in failures[0]

    class OkRadio:
        def play(self, name):
            return "ok"

    monkeypatch.setattr(R, "RadioPlayer", OkRadio)
    assert R.morning_routine() == []


def test_bedtime_routine_reports_offline_lamps(monkeypatch):
    import scheduler.routines as R

    monkeypatch.setattr(R, "speak", lambda text: None)
    from ai import llm
    monkeypatch.setattr(llm, "complete", lambda p, **kw: "welterusten")
    import voice.Whisper as W
    monkeypatch.setattr(W, "mic_available", lambda: False)

    class Lamp:
        ip = "192.0.2.9"

        async def connect(self):
            raise RuntimeError("time-out")

    monkeypatch.setattr(R, "_all_lamps", lambda: iter([Lamp()]))
    failures = R.bedtime_routine()
    assert failures == ["Lamp 192.0.2.9: time-out"]


def test_ai_tool_mentions_failed_steps(monkeypatch):
    import logic.gpt_handler as gh
    import scheduler.routines as R

    monkeypatch.setattr(R, "morning_routine", lambda: ["Radio: geen internet"])
    assert "geen internet" in gh.start_morning_routine()
    monkeypatch.setattr(R, "morning_routine", lambda: [])
    assert gh.start_morning_routine() == "Ochtend-routine gestart"


# --------------------------------------------------------------------------- #
# Leesbare lamp-fouten
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ('Http(reqwest::Error { kind: Request, url: "http://192.0.2.1/app", source: TimedOut })',
     "Lamp 192.0.2.1 is niet bereikbaar (time-out)"),
    ('Http(reqwest::Error { kind: Request, url: "http://192.168.2.15/app", source: hyper_util::client::legacy::Error(Connect, '
     'ConnectError("tcp connect error", Os { code: 111, kind: ConnectionRefused, message: "Connection refused" })) })',
     "Lamp 192.168.2.15 is niet bereikbaar (verbinding mislukt)"),
    ('Tapo(Unauthorized { kind: "SESSION_TIMEOUT", description: "Session timeout" })',
     "Lamp: sessie verlopen (opnieuw proberen)"),
    ('Tapo(Unauthorized { kind: "INVALID_CREDENTIALS" })',
     "Lamp: inloggen mislukt, controleer het Tapo-account in Settings"),
    ("iets heel anders", "iets heel anders"),
])
def test_describe_lamp_error(raw, expected):
    from devices.Lights import describe_lamp_error

    assert describe_lamp_error(RuntimeError(raw)) == expected


def test_lamp_endpoint_error_is_readable_not_raw_rust_text(client, monkeypatch):
    config.set("devices.lamps", [{"name": "A", "ip": "192.0.2.1"}])

    def boom(ip):
        raise RuntimeError('Http(reqwest::Error { kind: Request, url: "http://192.0.2.1/app", source: TimedOut })')

    monkeypatch.setattr(S, "lamp", boom)
    r = client.put("/api/lamp/on", json={"lamp": 0})
    assert r.status_code == 500
    assert r.get_json()["message"] == "Lamp 192.0.2.1 is niet bereikbaar (time-out)"
    r = client.get("/api/lamp/state?lamp=0")
    assert r.status_code == 503 and "niet bereikbaar" in r.get_json()["error"]


def test_routine_lamp_iteration_skips_entries_without_ip():
    import scheduler.routines as R

    config.set("devices.lamps", [{"name": "Leeg"}, {"name": "A", "ip": "192.0.2.1"}, {"name": "B", "ip": ""}])
    assert [l.ip for l in R._all_lamps()] == ["192.0.2.1"]


def test_greeting_falls_back_to_a_fixed_line_instead_of_speaking_the_llm_error(monkeypatch):
    """'Sorry, ik kan nu geen antwoord geven: HTTPConnectionPool(...)' werd om 07:00 hardop voorgelezen."""
    import scheduler.routines as R
    from ai import llm

    spoken = []
    monkeypatch.setattr(R, "speak", spoken.append)

    def down(prompt, **kw):
        raise ConnectionError("HTTPConnectionPool(host='localhost', port=11434): Max retries exceeded")

    monkeypatch.setattr(llm, "complete", down)

    class OkRadio:
        def play(self, name):
            return "ok"

    monkeypatch.setattr(R, "RadioPlayer", OkRadio)
    config.set("features.radio", True)
    failures = R.morning_routine()

    assert spoken[0] == "Goedemorgen!"
    assert not any("HTTPConnectionPool" in t or "Sorry" in t for t in spoken)
    assert len(failures) == 1 and failures[0].startswith("Greeting:")      # de mislukking wordt wel gemeld


def test_morning_notes_survive_a_note_without_timestamp(monkeypatch):
    import scheduler.routines as R
    from ai import llm

    spoken = []
    monkeypatch.setattr(R, "speak", spoken.append)
    monkeypatch.setattr(llm, "complete", lambda p, **kw: "hoi")
    monkeypatch.setattr(R, "get_notes", lambda cat: [{"note": "melk kopen"}])
    config.set("features.radio", False)
    assert R.morning_routine() == []
    assert "melk kopen" in spoken


def test_a_hanging_llm_does_not_delay_the_rest_of_the_morning_routine(monkeypatch):
    """Ollama accepteert de verbinding maar antwoordt niet (koude start / vastgelopen): de wekker moest
    tot de LLM-timeout (330 s) wachten voordat de radio aanging."""
    import threading
    import time

    import scheduler.routines as R
    from ai import llm

    spoken = []
    monkeypatch.setattr(R, "speak", spoken.append)
    release = threading.Event()
    monkeypatch.setattr(llm, "complete", lambda p, **kw: release.wait(30) or "te laat")
    monkeypatch.setattr(R, "_GREETING_WAIT_S", 0.3)
    order = []

    class Radio:
        def play(self, name):
            order.append("radio")
            return "ok"

    monkeypatch.setattr(R, "RadioPlayer", Radio)
    config.set("features.radio", True)
    t0 = time.monotonic()
    failures = R.morning_routine()
    took = time.monotonic() - t0
    release.set()

    assert took < 5, f"routine wachtte {took:.1f}s op het model"
    assert spoken[0] == "Goedemorgen!" and order == ["radio"]
    assert len(failures) == 1 and failures[0].startswith("Greeting:") and "antwoordde niet" in failures[0]
    assert "te laat" not in spoken            # het late antwoord wordt niet ineens alsnog uitgesproken


def test_a_quick_llm_answer_is_spoken_as_before(monkeypatch):
    import scheduler.routines as R
    from ai import llm

    spoken = []
    monkeypatch.setattr(R, "speak", spoken.append)
    monkeypatch.setattr(llm, "complete", lambda p, **kw: "  Goedemorgen, slaapkop!  ")
    R._say_generated("prompt", "vast")
    assert spoken == ["Goedemorgen, slaapkop!"]
