"""CSRF op de enige GET met bijwerkingen, en robuuste invoer op de auth-endpoints."""
import pytest

CHAT = "/api/chat_stream?message=zet+de+lamp+aan"


@pytest.fixture()
def spy(monkeypatch):
    """Registreert of de AI-pijplijn (met z'n tools) echt werd gestart."""
    import logic.gpt_handler as gh

    calls = []

    def fake_stream(text, session="voice"):
        calls.append(text)
        yield "ok"

    monkeypatch.setattr(gh, "verwerk_input_stream", fake_stream)
    return calls


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://evil.example"},
    {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_chat_stream_is_blocked(client, spy, headers):
    r = client.get(CHAT, headers=headers)
    assert r.status_code == 403
    assert spy == [], "de AI-tools mogen niet gestart worden door een andere site"


@pytest.mark.parametrize("headers", [
    {},                                             # curl/scripts sturen geen Origin
    {"Sec-Fetch-Site": "same-origin"},              # EventSource vanaf het dashboard zelf
    {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"},
])
def test_same_origin_and_scripted_chat_stream_still_works(client, spy, headers):
    r = client.get(CHAT, headers=headers)
    assert r.status_code == 200
    assert b"ok" in r.get_data()
    assert spy == ["zet de lamp aan"]


def test_plain_read_only_gets_are_unaffected(client):
    r = client.get("/api/config", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200        # alleen-lezen GET's blijven vrij (geen bijwerkingen)


def test_verify_with_a_numeric_code_is_a_401_not_a_500(client):
    r = client.post("/api/auth/verify", json={"code": 123456})
    assert r.status_code == 401
    assert r.get_json()["ok"] is False
    assert client.post("/api/auth/verify", json={"code": None}).status_code == 401
    assert client.post("/api/auth/verify", json={}).status_code == 401


def test_weatherapi_key_is_never_sent_over_plain_http(monkeypatch):
    """De API-key zit als query-parameter in de URL: over http:// staat 'ie in
    leesbare tekst op het netwerk."""
    from weer import weer as W

    seen = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"current": {"temp_c": 10, "wind_kph": 5, "humidity": 50, "condition": {"text": "Sunny"}},
                    "location": {"name": "Amsterdam"}}

    monkeypatch.setattr(W.requests, "get", lambda url, **kw: (seen.append(url), Resp())[1])
    W.WeerAPI()._fetch_weatherapi("Amsterdam")
    assert seen and all(u.startswith("https://") for u in seen), seen


@pytest.mark.parametrize("rid", ["a/b", "a b", "<img src=x>", "x" * 65, "../etc", "a?b"])
def test_routine_ids_must_be_url_and_html_safe(client, rid):
    r = client.post("/api/routines", json={"id": rid, "name": "x", "steps": []})
    assert r.status_code == 400


def test_normal_routine_ids_still_work(client):
    assert client.post("/api/routines", json={"id": "mijn-routine_2", "name": "Mijn", "steps": []}).status_code == 200
    ids = [r["id"] for r in client.get("/api/routines").get_json()["routines"]]
    assert "mijn-routine_2" in ids
    assert client.delete("/api/routines/mijn-routine_2").status_code == 200


def test_oversized_request_bodies_are_refused(client):
    """waitress' default is 1 GB per request (gespoold naar schijf)."""
    from Dashboard.backend.main import MAX_BODY_BYTES

    big = '{"note": "' + "x" * (MAX_BODY_BYTES + 10) + '"}'
    r = client.post("/api/notes", data=big, content_type="application/json")
    assert r.status_code == 413
    ok = client.post("/api/notes", json={"note": "gewoon"})
    assert ok.status_code == 200


def test_waitress_is_started_with_the_same_body_limit(monkeypatch):
    import rundashboard

    seen = {}

    class FakeWorker:
        def start(self):
            pass

    import waitress
    import Dashboard.backend.presence as presence_mod

    monkeypatch.setattr(presence_mod, "worker", FakeWorker())
    monkeypatch.setattr(waitress, "serve", lambda app, **kw: seen.update(kw))
    rundashboard.main()
    assert seen["max_request_body_size"] == rundashboard.MAX_BODY_BYTES
