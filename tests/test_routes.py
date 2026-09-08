"""Every read-only route answers without a 5xx, even with no Spotify/lamps/mail."""
import pytest

READ_ONLY = [
    "/", "/main", "/devices", "/media", "/environment", "/routines", "/notes",
    "/notifications", "/camera", "/chat", "/settings",
    "/favicon.ico",
    "/api/config", "/api/settings", "/api/system_stats", "/api/service_status",
    "/api/weather", "/api/calendar_today", "/api/overview", "/api/notes",
    "/api/routines", "/api/lamps", "/api/thermostat", "/api/camera_status",
    "/api/secrets", "/api/auth/status", "/api/current_playing",
    "/api/radio_stations", "/api/alarm",
]


@pytest.mark.parametrize("path", READ_ONLY)
def test_get_no_5xx(client, path):
    r = client.get(path)
    assert r.status_code < 500, f"{path} -> {r.status_code}"


def test_unknown_page_404(client):
    assert client.get("/zomaarwat").status_code == 404


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={"camera": {"fps": 7}})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert client.get("/api/config").get_json()["camera"]["enabled"] in (True, False)


def test_notes_crud(client):
    assert client.post("/api/notes", json={"note": "melk kopen"}).get_json()["success"]
    notes = client.get("/api/notes").get_json()["notes"]
    assert any("melk kopen" in n for n in notes)
    assert client.delete("/api/notes/0").get_json()["success"] is True


def test_thermostat_clamps(client):
    assert client.post("/api/thermostat", json={"target": 99}).get_json()["target"] == 30
    assert client.post("/api/thermostat", json={"target": "abc"}).status_code == 400


def test_secrets_never_leak_values(client, monkeypatch):
    monkeypatch.setenv("TAPO_PASSWORD", "supergeheim42")
    body = client.get("/api/secrets").get_json()
    assert "supergeheim42" not in str(body)
    assert body["secrets"]["TAPO_PASSWORD"]["set"] is True


def test_secrets_post_requires_unlock(client):
    assert client.post("/api/secrets", json={"OPENAI_API_KEY": "x"}).status_code == 403


def test_password_gate(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "geheim")
    assert b"Inloggen" in client.get("/settings").data          # login-pagina
    assert client.get("/main").status_code == 200               # niet beschermd
    assert client.get("/video_feed").status_code == 401
    ok = client.post("/api/login", json={"password": "geheim"})
    assert ok.get_json()["ok"] is True
    assert b"Inloggen" not in client.get("/settings").data      # nu de echte pagina
    assert client.post("/api/login", json={"password": "fout"}).status_code == 401


def test_routines_crud(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "x")
    client.post("/api/login", json={"password": "x"})
    # builtin routines altijd aanwezig
    r = client.get("/api/routines").get_json()["routines"]
    assert {x["id"] for x in r} >= {"morning", "bedtime", "party", "desk"}
    # custom aanmaken
    ok = client.post("/api/routines", json={
        "id": "filmavond", "name": "Filmavond", "desc": "dim",
        "steps": [{"action": "lamp", "lamp": 0, "mode": "desk"}],
    })
    assert ok.get_json()["success"] is True
    ids = {x["id"] for x in client.get("/api/routines").get_json()["routines"]}
    assert "filmavond" in ids
    # ingebouwde id weigeren
    assert client.post("/api/routines", json={"id": "morning", "name": "x"}).status_code == 400
    # verwijderen
    assert client.delete("/api/routines/filmavond").get_json()["success"] is True
    ids = {x["id"] for x in client.get("/api/routines").get_json()["routines"]}
    assert "filmavond" not in ids


def test_routines_write_needs_password(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "x")  # not logged in
    assert client.post("/api/routines", json={"id": "a", "name": "b"}).status_code == 401
    assert client.delete("/api/routines/a").status_code == 401


def test_alarm_set_and_clear(client):
    from Dashboard.backend import routines_api

    r = client.post("/api/alarm", json={"time": "07:30", "routine": "morning"})
    assert r.get_json()["success"] is True
    got = client.get("/api/alarm").get_json()
    assert got["set"] is True and got["time"] == "07:30" and got["routine"] == "morning"
    assert routines_api._alarm.alarm_time is not None

    assert client.post("/api/alarm", json={"time": "onzin"}).status_code == 400
    assert client.post("/api/alarm", json={"time": "07:30", "routine": "nope"}).status_code == 400

    assert client.delete("/api/alarm").get_json()["success"] is True
    assert client.get("/api/alarm").get_json()["set"] is False
    routines_api._alarm.cancel_alarm()
