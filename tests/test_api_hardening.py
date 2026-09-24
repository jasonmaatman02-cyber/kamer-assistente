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


def test_every_settings_page_field_passes_the_type_check():
    """De Settings-pagina bewaart sommige getalachtige velden als tekst (presence.lamp = "0" op de echte
    Pi). Lees het UI-schema en toets per veld een waarde van het TYPE dat de pagina echt stuurt -- zo
    kan de typecontrole nooit stilzwijgend het opslaan van de Settings-pagina breken."""
    import re
    from pathlib import Path

    from Dashboard.backend.system_api import _settings_type_errors

    src = (Path(__file__).resolve().parent.parent / "Dashboard" / "static" / "scripts" / "settings.js").read_text(encoding="utf-8")
    block = src[src.index("const SCHEMA"):src.index("function fieldId")]
    sample = {"bool": True, "number": 1, "list": ["a"], "text": "0", "select": "x", "password": "x", "textarea": "x"}
    section, fields = None, 0
    for line in block.split("\n"):
        m = re.search(r'\bkey:\s*"([a-z_]+)"', line)
        if m:
            section = m.group(1)
        m2 = re.search(r'\{\s*path:\s*"([^"]+)"[^}]*?\btype:\s*"([a-z]+)"', line)
        if m2 and section:
            fields += 1
            patch = {section: {}}
            node = patch[section]
            parts = m2.group(1).split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = sample[m2.group(2)]
            errors = _settings_type_errors(patch, config.DEFAULTS)
            assert not errors, f"{section}.{m2.group(1)} ({m2.group(2)}): {errors}"
    assert fields > 40, fields          # het schema is echt gelezen


def test_settings_saved_as_the_pi_has_them_are_accepted(client):
    """Werkelijke settings.json van de Pi (presence.lamp als tekst, kleine cijfers, routines)."""
    r = client.post("/api/settings", json={
        "camera": {"fps": 15, "browser_detection": True, "detect_threshold": 0.4, "lamp_quiet_from": "21:30", "lamp_quiet_to": "7:00"},
        "dashboard": {"poll_system_ms": 500}, "alarm": {"time": "07:00"},
        "presence": {"enabled": True, "interval_s": 4, "auto_light_enabled": True, "lamp": "0"},
        "devices": {"lamps": [{"name": "Bedroom Lamp", "ip": "192.168.2.2"}, {"name": "Desk Lamp", "ip": "192.168.2.26"}]},
    })
    assert r.status_code == 200, r.get_json()
    assert client.post("/api/settings", json={"presence": {"lamp": 1}}).status_code == 200
    assert client.post("/api/settings", json={"presence": {"lamp": True}}).status_code == 400


@pytest.mark.parametrize("url,body", [
    ("/api/seek", {"position_ms": "x"}),
    ("/api/seek", {"position_ms": [1]}),
    ("/api/set_volume", {"volume": "loud"}),
    ("/api/set_volume", {"volume": [50]}),
])
def test_non_numeric_seek_and_volume_are_a_400_not_a_500(client, url, body):
    r = client.post(url, json=body)
    assert r.status_code == 400 and r.get_json()["success"] is False


def test_seek_and_volume_are_clamped(client, monkeypatch):
    from Dashboard.backend import services as S

    seen = {}

    class SP:
        def seek_track(self, pos):
            seen["seek"] = pos

    class DJ:
        sp = SP()
        last_error = None

        def current_track(self):
            return {"type": "spotify"}

        def set_volume(self, v):
            seen["vol"] = v
            return True

    monkeypatch.setattr(S, "sp_dj", lambda: DJ())
    monkeypatch.setattr(S, "svc", lambda name: DJ() if name == "spotify" else None)
    assert client.post("/api/seek", json={"position_ms": -500}).status_code == 200
    assert client.post("/api/set_volume", json={"volume": 250}).get_json()["success"] is True
    assert seen == {"seek": 0, "vol": 100}


@pytest.mark.parametrize("url", ["/api/radio_stop", "/api/radio_pause", "/api/radio_resume"])
def test_radio_controls_without_a_radio_service_are_not_reported_as_success(client, monkeypatch, url):
    from Dashboard.backend import services as S

    monkeypatch.setattr(S, "svc", lambda name: None)
    r = client.post(url)
    assert r.status_code == 503 and r.get_json()["success"] is False


def test_playlist_tracks_cannot_loop_forever_on_a_pager_that_never_ends(client, monkeypatch):
    from Dashboard.backend import services as S

    calls = {"n": 0}

    class SP:
        def playlist(self, pid, fields=None):
            return {"name": "x", "images": []}

        def playlist_items(self, pid, **kw):
            calls["n"] += 1
            return {"items": [], "next": "https://api.spotify.com/eindeloos"}      # nooit klaar, nooit items

    class DJ:
        sp = SP()

    monkeypatch.setattr(S, "sp_dj", lambda: DJ())
    r = client.get("/api/playlist_tracks/abc")
    assert r.status_code == 200 and r.get_json()["tracks"] == []
    assert calls["n"] == 1                                    # leeg blok: stoppen

    calls["n"] = 0

    class SP2(SP):
        def playlist_items(self, pid, **kw):
            calls["n"] += 1
            return {"items": [{"track": {"type": "track", "uri": f"spotify:track:{calls['n']}", "id": "i", "name": "n",
                                         "artists": [], "duration_ms": 1000, "album": {"images": []}}}], "next": "meer"}

    DJ.sp = SP2()
    r = client.get("/api/playlist_tracks/abc")
    assert r.status_code == 200 and calls["n"] == 10          # begrensd op 10 pagina's
