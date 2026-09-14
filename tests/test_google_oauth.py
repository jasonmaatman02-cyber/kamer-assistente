"""Google Agenda OAuth-koppeling (/api/google_calendar/auth-url + /token).

Achtergrond van de bug die dit test-bestand vastlegt: de gebruiker meldde
dat de volledige callback-URL (met state/iss/code/scope) "niet goed
verwerkt leek te worden" na een geslaagde Google-login. Live onderzoek wees
géén UI-lengtebeperking aan als daadwerkelijke oorzaak, maar twee echte
bugs in de OAuth-uitwisseling zelf:

1. oauthlib weigert standaard elke http-redirect-URI (InsecureTransportError)
   -- de code zette OAUTHLIB_INSECURE_TRANSPORT nergens, dus fetch_token()
   faalde altijd, voor elke geplakte URL, ongeacht de inhoud.
2. auth-url en token-uitwisseling zijn twee losse HTTP-requests, dus twee
   losse Flow-objecten. Zonder de CSRF-state expliciet tussen die twee te
   bewaren en opnieuw mee te geven, wordt 'ie bij het inwisselen
   stilzwijgend NIET gevalideerd (geen foutmelding, gewoon geen check).

Geen echte netwerkcall naar Google hier: Flow.fetch_token() zelf faalt al
lokaal (dus zonder netwerk) op een foute state -- dat testen we met de
ECHTE, ongemockte library. Voor het geslaagde pad wordt alleen de laatste
netwerkstap (het token-endpoint) gemockt.
"""
import time


def _unlock(monkeypatch):
    from Dashboard.backend import auth as auth_mod

    token = "test-unlock-token"
    monkeypatch.setitem(auth_mod._unlock_sessions, token, time.time() + 3600)
    return {"X-Unlock-Token": token}


def _configure_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret-super-geheim")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/callback")


def test_loopback_redirect_is_detected():
    from Dashboard.backend.services import _is_loopback_redirect

    assert _is_loopback_redirect("http://127.0.0.1:8000/callback") is True
    assert _is_loopback_redirect("http://localhost:8000/callback") is True
    assert _is_loopback_redirect("https://mijn-echte-domein.nl/callback") is False
    assert _is_loopback_redirect("http://192.168.2.30:5000/callback") is False


def test_auth_url_stores_state_for_later_verification(client, monkeypatch):
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True
    assert "accounts.google.com" in r["url"]

    from Dashboard.backend import services as S
    assert S._google_oauth_state["value"]   # bewaard voor de token-stap


def test_full_callback_url_with_correct_state_extracts_code(client, monkeypatch):
    """De regressietest die expliciet gevraagd is: een volledige callback-URL
    met state, iss, code EN scope -- en de authorization code moet correct
    bij fetch_token() terechtkomen."""
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True
    from Dashboard.backend import services as S
    real_state = S._google_oauth_state["value"]

    seen = {}

    def fake_fetch_token(self, authorization_response=None, **kw):
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(authorization_response).query)
        seen["code"] = qs.get("code", [None])[0]
        seen["state"] = qs.get("state", [None])[0]
        seen["iss"] = qs.get("iss", [None])[0]
        seen["scope"] = qs.get("scope", [None])[0]
        # Flow.credentials is een read-only property die dit leest van de
        # onderliggende sessie -- realistisch genoeg vullen zodat
        # flow.credentials.to_json() in de echte route-code werkt.
        self.oauth2session.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "scope": ["https://www.googleapis.com/auth/calendar.readonly"],
            "token_type": "Bearer",
            "expires_at": time.time() + 3600,
        }

    from google_auth_oauthlib.flow import Flow
    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)

    callback_url = (
        f"http://127.0.0.1:8000/callback?state={real_state}"
        "&iss=https%3A%2F%2Faccounts.google.com"
        "&code=4%2F0AVeryLongRealisticAuthorizationCode-example_1234567890"
        "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.readonly"
    )
    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": callback_url})
    body = resp.get_json()
    assert body["ok"] is True, body

    # de code (en de andere queryparams) zijn correct en ongeschonden aangekomen
    assert seen["code"] == "4/0AVeryLongRealisticAuthorizationCode-example_1234567890"
    assert seen["state"] == real_state
    assert seen["iss"] == "https://accounts.google.com"
    assert seen["scope"] == "https://www.googleapis.com/auth/calendar.readonly"


def test_mismatching_state_is_rejected(client, monkeypatch):
    """Echte, ongemockte CSRF-statecheck: een andere state dan die bij de
    auth-url hoorde moet geweigerd worden -- geen netwerkcall nodig, want
    oauthlib faalt hier al lokaal."""
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True

    tampered_url = (
        "http://127.0.0.1:8000/callback?state=EEN_ANDERE_STATE_DAN_VERWACHT"
        "&iss=https%3A%2F%2Faccounts.google.com"
        "&code=zomaar-een-code"
        "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.readonly"
    )
    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": tampered_url})
    body = resp.get_json()
    assert body["ok"] is False
    assert "state" in body["error"].lower() or "csrf" in body["error"].lower()


def test_token_without_prior_auth_url_is_rejected(client, monkeypatch):
    """Direct /token aanroepen zonder eerst /auth-url (dus geen bewaarde
    state) moet netjes falen, niet stilzwijgend de check overslaan."""
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": "http://127.0.0.1:8000/callback?state=x&code=y"})
    body = resp.get_json()
    assert body["ok"] is False
    assert "geen lopende" in body["error"].lower()


def test_oauth_secrets_never_leak_into_error_response(client, monkeypatch):
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True

    tampered_url = "http://127.0.0.1:8000/callback?state=nope&code=abc"
    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": tampered_url})
    body_text = str(resp.get_json())
    assert "test-client-secret-super-geheim" not in body_text
