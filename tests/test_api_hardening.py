"""Ongeldige invoer / ontbrekende diensten geven nette JSON-fouten, en radio/kleur-fixes."""
import colorsys

import pytest

import config
from devices.Lights import hex_to_hue_saturation


# --------------------------------------------------------------------------- #
# Lamp-endpoints: invoer valideren
# --------------------------------------------------------------------------- #
@pytest.fixture()
def lamp_calls(monkeypatch):
    """Nep-lamp: registreert wat er echt naar de lamp zou gaan."""
    from Dashboard.backend import services as S

    calls = []

    class FakeLamp:
        async def zet_helderheid(self, b):
            calls.append(("bri", b))
            return "ok"

        async def zet_kleur_temp(self, t):
            calls.append(("ct", t))
            return "ok"

        async def zet_kleur(self, c):
            calls.append(("color", c))
            return "ok"

    dropped = []
    monkeypatch.setattr(S, "lamp", lambda ip: FakeLamp())
    monkeypatch.setattr(S, "drop_lamp", lambda ip: dropped.append(ip))
    config.set("devices.lamps", [{"name": "A", "ip": "192.0.2.11"}])
    return calls, dropped


@pytest.mark.parametrize("url,body", [
    ("/api/lamp/brightness", {"brightness": "abc"}),
    ("/api/lamp/brightness", {"brightness": None}),
    ("/api/lamp/colortemp", {"color_temp": "warm"}),
    ("/api/lamp/color", {}),
    ("/api/lamp/color", {"color": 12}),
    ("/api/lamp/color", {"color": "#zzzzzz"}),
    ("/api/lamp/color", {"color": "#fff"}),
    ("/api/lamp/color", {"color": "geen-kleur"}),
])
def test_invalid_lamp_input_is_a_json_400_and_does_not_touch_the_lamp(client, lamp_calls, url, body):
    calls, dropped = lamp_calls
    r = client.put(url, json=body)
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    assert r.is_json and r.get_json()["status"] == "error"
    assert calls == [] and dropped == []        # geen commando, en de verbinding blijft heel


def test_valid_lamp_input_is_clamped_to_what_the_lamp_accepts(client, lamp_calls):
    calls, _ = lamp_calls
    assert client.put("/api/lamp/brightness", json={"brightness": 0}).status_code == 200
    assert client.put("/api/lamp/brightness", json={"brightness": 250}).status_code == 200
    assert client.put("/api/lamp/colortemp", json={"color_temp": 100}).status_code == 200
    assert client.put("/api/lamp/colortemp", json={"color_temp": 99999}).status_code == 200
    assert client.put("/api/lamp/color", json={"color": "#FFCC00"}).status_code == 200
    assert client.put("/api/lamp/color", json={"color": "Rood"}).status_code == 200
    assert calls == [("bri", 1), ("bri", 100), ("ct", 2500), ("ct", 6500), ("color", "#FFCC00"), ("color", "rood")]


# --------------------------------------------------------------------------- #
# Kleurconversie hex -> Tapo (HSV)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hex_,expected", [
    ("#ff0000", (0, 100)),
    ("#ffcc00", (48, 100)),
    ("#00ff00", (120, 100)),
    ("#ff9999", (0, 40)),          # pastel: HLS-saturatie gaf hier 100 (volle rode lamp)
    ("#ffffff", (0, 0)),
    ("#808080", (0, 0)),
    ("#0000ff", (240, 100)),
])
def test_hex_to_hue_saturation_is_hsv(hex_, expected):
    assert hex_to_hue_saturation(hex_) == expected


def test_hex_hue_round_trips_through_hsv():
    for h in range(0, 360, 15):
        for s in (30, 60, 100):
            r, g, b = colorsys.hsv_to_rgb(h / 360, s / 100, 1)
            hx = "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
            got_h, got_s = hex_to_hue_saturation(hx)
            assert abs(((got_h - h + 180) % 360) - 180) <= 2 and abs(got_s - s) <= 2, (hx, h, s, got_h, got_s)


# --------------------------------------------------------------------------- #
# Spotify niet ingesteld: nette 503-JSON in plaats van HTML-500
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method,url,body", [
    ("get", "/api/playlist_tracks/abc", None),
    ("post", "/api/spotify_pause", None),
    ("post", "/api/spotify_next", None),
    ("post", "/api/play_playlist", {"id": "x"}),
    ("post", "/api/play_track", {"track_id": "x"}),
    ("post", "/api/seek", {"position_ms": 1000}),
])
def test_missing_spotify_is_a_json_503(client, method, url, body):
    r = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert r.status_code == 503, (r.status_code, r.get_data(as_text=True)[:200])
    assert r.is_json
    assert "Spotify" in (r.get_json().get("error") or "")


# --------------------------------------------------------------------------- #
# Radio: mislukte start is geen "success"; onbekend station laat de lopende zender met rust
# --------------------------------------------------------------------------- #
class _Radio:
    def __init__(self, outcome):
        self.outcome = outcome
        self.last_error = None
        self.stations = {"a": "x"}

    def play(self, name):
        self.last_error = self.outcome
        return self.outcome or f"Speelt nu: {name}"

    def current_station(self):
        return {"type": "none", "station": None}


@pytest.mark.parametrize("outcome,status", [
    (None, 200),
    ("Station 'nope' niet gevonden", 404),
    ("Radio kon niet starten (check URL of internet).", 502),
])
def test_radio_play_reports_failures(client, monkeypatch, outcome, status):
    from Dashboard.backend import services as S

    fake = _Radio(outcome)
    monkeypatch.setattr(S, "svc", lambda name: fake if name == "radio" else None)
    r = client.post("/api/radio_play", json={"station": "nope"})
    assert r.status_code == status
    assert r.get_json()["success"] is (status == 200)
    if status != 200:
        assert r.get_json()["error"] == outcome


def test_unknown_station_does_not_stop_the_running_radio():
    from tests.test_radio import _player

    import vlc

    p = _player([vlc.State.Playing])
    result = p.play("nietbestaand")
    assert "niet gevonden" in result and p.last_error
    assert p.player.stopped is False, "een onbekende zender stopte de lopende radio"
    result = p.play("")
    assert p.player.stopped is False and p.last_error


def test_successful_play_clears_last_error():
    import vlc

    from tests.test_radio import _player

    p = _player([vlc.State.Playing])
    p.play("nietbestaand")
    assert p.last_error
    assert p.play("radio538").startswith("Speelt nu")
    assert p.last_error is None


# --------------------------------------------------------------------------- #
# system_stats: niet elke poll 200ms blokkeren
# --------------------------------------------------------------------------- #
def test_system_stats_is_cached_between_polls(monkeypatch):
    import psutil

    from Dashboard.backend import services as S

    calls = []

    def fake_cpu(interval=None):
        calls.append(interval)
        return 12.5

    monkeypatch.setattr(psutil, "cpu_percent", fake_cpu)
    first = S.system_stats()
    for _ in range(20):
        assert S.system_stats() == first
    assert len(calls) == 1
    assert first["cpu_percent"] == 12.5


@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN", '"warm"', "null"])
def test_thermostat_rejects_unusable_targets_with_400(client, raw):
    r = client.post("/api/thermostat", data=f'{{"target": {raw}}}', content_type="application/json")
    assert r.status_code == 400, r.get_data(as_text=True)[:200]
    assert r.is_json


# --------------------------------------------------------------------------- #
# Settings: typecontrole tegen DEFAULTS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("patch", [
    {"camera": {"fps": "snel"}},
    {"camera": {"fps": None}},
    {"camera": {"fps": True}},
    {"camera": {"enabled": "ja"}},
    {"camera": "aan"},
    {"presence": {"interval_s": [3]}},
    {"weather": {"city": 5}},
    {"weather": {"city": "x" * 6000}},
    {"devices": {"lamps": "geen-lijst"}},
    {"devices": {"lamps": [{"name": "A", "ip": 5}]}},
    {"devices": {"lamps": [1, 2]}},
    {"ai": {"temperature": "warm"}},
])
def test_settings_patch_with_wrong_types_is_rejected_and_not_saved(client, patch):
    before = client.get("/api/settings").get_json()["settings"]
    r = client.post("/api/settings", json=patch)
    assert r.status_code == 400 and r.get_json()["success"] is False
    assert client.get("/api/settings").get_json()["settings"] == before


def test_settings_patch_with_right_types_still_works(client):
    r = client.post("/api/settings", json={
        "camera": {"fps": 7.5, "enabled": False}, "weather": {"city": "Utrecht"},
        "presence": {"interval_s": 4}, "routines": [{"id": "x"}], "onbekende": {"sleutel": 1},
        "devices": {"lamps": [{"name": "Bureau", "ip": ""}]},
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    cfg = r.get_json()["settings"]
    assert cfg["camera"]["fps"] == 7.5 and cfg["weather"]["city"] == "Utrecht"
    assert cfg["devices"]["lamps"] == [{"name": "Bureau", "ip": ""}]


def test_empty_or_non_object_patch_is_a_400(client):
    assert client.post("/api/settings", json={}).status_code == 400
    assert client.post("/api/settings", json=[1]).status_code == 400


def test_roundtrip_of_all_current_settings_is_accepted(client):
    """De Settings-pagina stuurt de hele boom terug; de typecontrole mag dat nooit weigeren."""
    current = client.get("/api/settings").get_json()["settings"]
    r = client.post("/api/settings", json=current)
    assert r.status_code == 200, r.get_json()
    assert client.get("/api/settings").get_json()["settings"] == current


def test_lamp_ip_resolution():
    from Dashboard.backend import services as S

    config.set("devices.lamps", [{"name": "Bureau", "ip": "192.0.2.1"}, {"name": "Slaapkamer", "ip": "192.0.2.2"}, {"name": "Leeg"}])
    assert S.lamp_ip(0) == "192.0.2.1" and S.lamp_ip("1") == "192.0.2.2" and S.lamp_ip(1) == "192.0.2.2"
    assert S.lamp_ip("slaap") == "192.0.2.2"
    assert S.lamp_ip("onbekende naam") == "192.0.2.1"          # niet-passende naam: eerste lamp (ongewijzigd)
    assert S.lamp_ip(5) is None and S.lamp_ip("9") is None and S.lamp_ip(-1) is None
    assert S.lamp_ip(2) is None                               # lamp zonder ip
    config.set("devices.lamps", [])
    assert S.lamp_ip(0) is None and S.lamp_ip("x") is None


def test_lamp_endpoint_with_an_index_that_does_not_exist_does_not_act_on_lamp_zero(client, monkeypatch):
    from Dashboard.backend import services as S

    config.set("devices.lamps", [{"name": "A", "ip": "192.0.2.1"}])
    touched = []
    monkeypatch.setattr(S, "lamp", lambda ip: touched.append(ip) or pytest.fail("lamp mag niet gebruikt worden"))
    r = client.put("/api/lamp/on", json={"lamp": 5})
    assert r.status_code == 400 and r.get_json()["message"] == "geen lamp geconfigureerd"
    assert touched == []
