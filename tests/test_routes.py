"""Every read-only route answers without a bug, even with no Spotify/lamps/mail."""
import time
import pytest

READ_ONLY = [
    "/", "/main", "/devices", "/media", "/environment", "/routines", "/notes",
    "/notifications", "/camera", "/chat", "/settings",
    "/favicon.ico",
    "/api/config", "/api/settings", "/api/system_stats", "/api/service_status",
    "/api/weather", "/api/calendar_today", "/api/overview", "/api/notes",
    "/api/routines", "/api/lamps", "/api/thermostat", "/api/camera_status",
    "/api/secrets", "/api/auth/status", "/api/current_playing",
    "/api/radio_stations", "/api/alarm", "/api/speedtest", "/api/health",
    "/api/camera_snapshot",
]


@pytest.mark.parametrize("path", READ_ONLY)
def test_get_no_crash(client, path):
    r = client.get(path)
    # 500 = onafgevangen bug. 502/503 = bewuste "dependency offline" (bv. weer
    # zonder internet in de testomgeving) en dus oké.
    assert r.status_code != 500 and r.status_code < 504, f"{path} -> {r.status_code}"


def test_unknown_page_404(client):
    assert client.get("/zomaarwat").status_code == 404


def test_health_shape(client):
    h = client.get("/api/health").get_json()
    assert h["ok"] is True
    for key in ("git", "git_running", "restart_pending", "python", "server",
                "system", "services", "camera", "alarm", "threads"):
        assert key in h
    assert isinstance(h["threads"], list)
    assert isinstance(h["restart_pending"], bool)


def test_chat_reset_clears_server_session(client, monkeypatch):
    import ai.llm as llm
    from logic import gpt_handler
    from Dashboard.backend.chat_api import _sid

    monkeypatch.setattr(llm, "chat_stream",
                        lambda m, tools=None: iter([{"type": "done", "content": "ok"}]))
    with client.application.test_request_context("/api/chat/reset"):
        sid = _sid("tab1")                           # zelfde sleutel als het endpoint
    list(gpt_handler.verwerk_input_stream("hoi", session=sid))
    assert len(gpt_handler.history(sid)) > 1
    assert client.post("/api/chat/reset", json={"sid": "tab1"}).get_json()["ok"] is True
    assert len(gpt_handler.history(sid)) == 1        # alleen de system-prompt


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={"camera": {"fps": 7}})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert client.get("/api/config").get_json()["camera"]["enabled"] in (True, False)


def test_camera_detect_threshold_roundtrip(client):
    assert client.post("/api/settings", json={"camera": {"detect_threshold": 0.35}}).status_code == 200
    assert client.get("/api/config").get_json()["camera"]["detect_threshold"] == 0.35


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


def test_request_code_rate_limited(client, monkeypatch):
    from Dashboard.backend import auth

    auth._code_rl.update(last=0.0, day="", count=0)
    monkeypatch.setattr(auth, "mail_ready", lambda: True)
    sent = []
    monkeypatch.setattr("logic.mail_sender.send_email_message", lambda *a, **k: sent.append(1) or "ok")

    assert client.post("/api/auth/request-code").get_json()["ok"] is True
    # tweede meteen erna -> 429, geen extra mail
    r = client.post("/api/auth/request-code")
    assert r.status_code == 429 and r.get_json()["ok"] is False
    assert len(sent) == 1

    # dagcap
    auth._code_rl.update(last=0.0, count=auth._CODE_DAILY_MAX)
    assert client.post("/api/auth/request-code").status_code == 429


def test_speedtest_flow(client, monkeypatch):
    import time as _t

    from Dashboard.backend import system_api

    with system_api._speed_lock:
        system_api._speed.update(status="idle", result=None, error=None)

    def fake_run():
        with system_api._speed_lock:
            system_api._speed.update(status="done", error=None, result={
                "down_mbps": 42.0, "up_mbps": 9.1, "ping_ms": 12.0,
                "server": "Test", "tested_at": "2026-09-08 21:00"})

    monkeypatch.setattr(system_api, "_run_speedtest", fake_run)

    assert client.get("/api/speedtest").get_json()["status"] == "idle"
    assert client.post("/api/speedtest").get_json()["status"] == "running"
    for _ in range(40):
        if client.get("/api/speedtest").get_json()["status"] == "done":
            break
        _t.sleep(0.05)
    r = client.get("/api/speedtest").get_json()
    assert r["status"] == "done" and r["result"]["down_mbps"] == 42.0


def test_speedtest_start_is_open(client, monkeypatch):
    # de kaart staat op de open Overview -> geen wachtwoord nodig, ook niet
    # als er er een gezet is
    monkeypatch.setenv("DASHBOARD_PASSWORD", "x")
    from Dashboard.backend import system_api

    monkeypatch.setattr(system_api, "_run_speedtest", lambda: None)
    with system_api._speed_lock:
        system_api._speed.update(status="idle", result=None, error=None)
    assert client.post("/api/speedtest").get_json()["status"] == "running"


def test_login_is_session_only_by_default(client, monkeypatch):
    import config
    from Dashboard.backend import auth

    monkeypatch.setenv("DASHBOARD_PASSWORD", "geheim")

    config.set("security.session_days", 0)
    r = client.post("/api/login", json={"password": "geheim"})
    sc = r.headers.get("Set-Cookie", "")
    assert r.get_json()["persistent"] is False
    assert "Max-Age" not in sc and "Expires" not in sc          # sessiecookie
    tok = next(iter(auth._pw_sessions))
    assert auth._pw_sessions[tok] - time.time() <= 24 * 3600 + 5  # harde serverlimiet

    auth._pw_sessions.clear()
    config.set("security.session_days", 7)
    r = client.post("/api/login", json={"password": "geheim"})
    assert r.get_json()["persistent"] is True
    assert "Max-Age=604800" in r.headers.get("Set-Cookie", "")


def test_settings_post_needs_password(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "geheim")
    assert client.post("/api/settings", json={"camera": {"fps": 9}}).status_code == 401
    client.post("/api/login", json={"password": "geheim"})
    assert client.post("/api/settings", json={"camera": {"fps": 9}}).status_code == 200


def test_csrf_guard_blocks_foreign_origin(client):
    # zelfde-origin / geen Origin -> ok
    assert client.post("/api/alarm", json={"time": "07:00"}).status_code in (200, 400)
    client.delete("/api/alarm")
    # andere site -> 403, nog voor de route draait
    r = client.post("/api/alarm", json={"time": "07:00"},
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


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
