"""Google Agenda OAuth-koppeling (/api/google_calendar/auth-url + /token).

Achtergrond van de bugs die dit bestand vastlegt -- twee losse HTTP-requests
(dus twee losse Flow-objecten) voor auth-url en token-uitwisseling:

1. oauthlib weigert standaard elke http-redirect-URI (InsecureTransportError)
   -- gefixt door OAUTHLIB_INSECURE_TRANSPORT alleen voor een loopback-
   redirect-URI toe te staan.
2. De CSRF-state werd niet bewaard tussen de twee requests -> de check werd
   stilzwijgend overgeslagen. Gefixt: state wordt bewaard en meegegeven aan
   het tweede Flow-object.
3. (deze fix) De PKCE code_verifier die Flow.authorization_url() op het EERSTE
   Flow-object genereert (flow.code_verifier) werd nergens bewaard, dus
   stuurde het tweede Flow-object 'm als None mee -> Google weigerde met
   "invalid_grant: Missing code verifier". state en code_verifier worden nu
   samen bewaard/uitgelezen (S.set_google_oauth_pending/pop_google_oauth_pending)
   en beide teruggegeven aan het tweede Flow-object.

Waarom de eerdere tests dit niet opvingen: de "succesvolle uitwisseling"-test
verving Flow.fetch_token() volledig door een eigen fake, die nooit de echte
code_verifier-doorgifte-logica uitvoerde. Hieronder mockt
test_token_exchange_uses_saved_code_verifier() in plaats daarvan alleen de
onderste HTTP-laag (requests.Session.send) -- Flow.fetch_token() en
OAuth2Session.fetch_token() draaien dus ECHT, inclusief de code die de
verifier in de POST-body zet. Dat is precies hoe deze bug empirisch
bevestigd is (zie de commit-boodschap) en had 'm destijds gevonden.
"""
import json
import time

import requests


def _unlock(monkeypatch):
    from Dashboard.backend import auth as auth_mod

    token = "test-unlock-token"
    monkeypatch.setitem(auth_mod._unlock_sessions, token, time.time() + 3600)
    return {"X-Unlock-Token": token}


def _configure_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret-super-geheim")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/callback")


def _mock_token_endpoint(monkeypatch):
    """Mockt alleen de daadwerkelijke HTTP-transportlaag (requests.Session.send)
    -- alles daarboven (Flow.fetch_token, OAuth2Session.fetch_token, oauthlib's
    eigen state-check en request-bodyopbouw) draait ECHT. `captured['body']`
    bevat na afloop de werkelijke, verzonden application/x-www-form-urlencoded
    POST-body naar Google's tokenendpoint."""
    captured = {}

    def fake_send(self, prepared_request, **kw):
        captured["body"] = prepared_request.body
        resp = requests.Response()
        resp.status_code = 200
        resp.headers["content-type"] = "application/json"
        resp._content = json.dumps({
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/calendar.readonly",
            "token_type": "Bearer",
        }).encode()
        resp.request = prepared_request
        return resp

    monkeypatch.setattr(requests.Session, "send", fake_send)
    return captured


def _callback_url(state: str, code: str = "4/0AVeryLongRealisticAuthorizationCode-example") -> str:
    return (
        f"http://127.0.0.1:8000/callback?state={state}"
        "&iss=https%3A%2F%2Faccounts.google.com"
        f"&code={code}"
        "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.readonly"
    )


def test_loopback_redirect_is_detected():
    from Dashboard.backend.services import _is_loopback_redirect

    assert _is_loopback_redirect("http://127.0.0.1:8000/callback") is True
    assert _is_loopback_redirect("http://localhost:8000/callback") is True
    assert _is_loopback_redirect("https://mijn-echte-domein.nl/callback") is False
    assert _is_loopback_redirect("http://192.168.2.30:5000/callback") is False


# --------------------------------------------------------------------------- #
# 1. Auth-URL genereren met PKCE
# --------------------------------------------------------------------------- #
def test_auth_url_includes_pkce_code_challenge(client, monkeypatch):
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True
    assert "accounts.google.com" in r["url"]
    assert "code_challenge=" in r["url"]
    assert "code_challenge_method=S256" in r["url"]


# --------------------------------------------------------------------------- #
# 2. state én code_verifier worden opgeslagen
# --------------------------------------------------------------------------- #
def test_auth_url_stores_state_and_code_verifier(client, monkeypatch):
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True

    from Dashboard.backend import services as S
    assert S._google_oauth_pending["state"]
    assert S._google_oauth_pending["code_verifier"]
    assert len(S._google_oauth_pending["code_verifier"]) >= 43   # PKCE-minimum (RFC 7636)


# --------------------------------------------------------------------------- #
# 3 + 6. De token-request gebruikt dezelfde code_verifier + volledige
# callback-URL (state/iss/code/scope) -- met de ECHTE Flow-methode, alleen
# de HTTP-transportlaag gemockt (zie _mock_token_endpoint hierboven).
# --------------------------------------------------------------------------- #
def test_token_exchange_uses_saved_code_verifier(client, monkeypatch):
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True
    from Dashboard.backend import services as S
    real_state = S._google_oauth_pending["state"]
    real_verifier = S._google_oauth_pending["code_verifier"]

    captured = _mock_token_endpoint(monkeypatch)

    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": _callback_url(real_state)})
    body = resp.get_json()
    assert body["ok"] is True, body

    # de échte POST-body naar Google bevat de EXACTE, bij de auth-url
    # gegenereerde code_verifier -- dit is precies waar "invalid_grant:
    # Missing code verifier" vandaan kwam toen dit ontbrak.
    assert f"code_verifier={real_verifier}" in captured["body"]
    assert "grant_type=authorization_code" in captured["body"]
    assert "code=4%2F0AVeryLongRealisticAuthorizationCode-example" in captured["body"]


# --------------------------------------------------------------------------- #
# 4. Een ontbrekende verifier geeft een duidelijke fout
# --------------------------------------------------------------------------- #
def test_token_without_prior_auth_url_is_rejected(client, monkeypatch):
    """Direct /token aanroepen zonder eerst /auth-url (dus geen bewaarde
    state/verifier) moet netjes falen, niet stilzwijgend de check overslaan."""
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": _callback_url("x")})
    body = resp.get_json()
    assert body["ok"] is False
    assert "geen lopende" in body["error"].lower()


def test_token_with_state_but_no_verifier_is_rejected(client, monkeypatch):
    """Los van hoe het zou kunnen ontstaan (bv. toekomstige codewijziging die
    de state wel maar de verifier niet bewaart): als er wél een state maar
    GEEN verifier klaarstaat, moet dat ook een duidelijke fout geven i.p.v.
    een fetch_token()-poging met code_verifier=None."""
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    from Dashboard.backend import services as S
    S.set_google_oauth_pending("een-state-zonder-verifier", "")   # verifier ontbreekt

    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": _callback_url("een-state-zonder-verifier")})
    body = resp.get_json()
    assert body["ok"] is False
    assert "geen lopende" in body["error"].lower()


# --------------------------------------------------------------------------- #
# 5. Een oude/verkeerde verifier wordt niet geaccepteerd (single-use)
# --------------------------------------------------------------------------- #
def test_pending_pair_is_single_use(client, monkeypatch):
    """state+code_verifier worden nooit los van elkaar en nooit twee keer
    gebruikt: na één /token-poging (geslaagd of niet) is er niets meer over
    om een tweede, latere poging mee te laten slagen met verouderde data."""
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True
    from Dashboard.backend import services as S
    real_state = S._google_oauth_pending["state"]

    _mock_token_endpoint(monkeypatch)
    first = client.post("/api/google_calendar/token", headers=headers,
                         json={"redirect_url": _callback_url(real_state)}).get_json()
    assert first["ok"] is True

    # tweede poging (zelfde of een andere URL) vindt niks meer klaarstaan
    second = client.post("/api/google_calendar/token", headers=headers,
                          json={"redirect_url": _callback_url(real_state)}).get_json()
    assert second["ok"] is False
    assert "geen lopende" in second["error"].lower()


# --------------------------------------------------------------------------- #
# 7. Bestaande CSRF-state-validatie blijft werken
# --------------------------------------------------------------------------- #
def test_mismatching_state_is_rejected(client, monkeypatch):
    """Echte, ongemockte CSRF-statecheck: een andere state dan die bij de
    auth-url hoorde moet geweigerd worden -- geen netwerkcall nodig, want
    oauthlib faalt hier al lokaal (vóór de code_verifier ooit relevant wordt)."""
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True

    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": _callback_url("EEN_ANDERE_STATE_DAN_VERWACHT")})
    body = resp.get_json()
    assert body["ok"] is False
    assert "state" in body["error"].lower() or "csrf" in body["error"].lower()


# --------------------------------------------------------------------------- #
# 8. Geen secrets/verifiers/codes worden gelogd
# --------------------------------------------------------------------------- #
def test_oauth_secrets_never_leak_into_error_response(client, monkeypatch):
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True
    from Dashboard.backend import services as S
    real_verifier = S._google_oauth_pending["code_verifier"]

    resp = client.post("/api/google_calendar/token", headers=headers,
                        json={"redirect_url": _callback_url("EEN_VERKEERDE_STATE")})
    body_text = str(resp.get_json())
    assert "test-client-secret-super-geheim" not in body_text
    assert real_verifier not in body_text


def test_code_verifier_and_secrets_never_printed(client, monkeypatch, capsys):
    _configure_google(monkeypatch)
    headers = _unlock(monkeypatch)

    r = client.get("/api/google_calendar/auth-url", headers=headers).get_json()
    assert r["ok"] is True
    from Dashboard.backend import services as S
    real_state = S._google_oauth_pending["state"]
    real_verifier = S._google_oauth_pending["code_verifier"]

    _mock_token_endpoint(monkeypatch)
    client.post("/api/google_calendar/token", headers=headers,
                json={"redirect_url": _callback_url(real_state)})

    out = capsys.readouterr()
    assert real_verifier not in out.out and real_verifier not in out.err
    assert "test-client-secret-super-geheim" not in out.out
    assert "test-client-secret-super-geheim" not in out.err
