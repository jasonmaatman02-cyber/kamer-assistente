"""Agenda: Google-tokenverversing heeft een eigen (korte) timeout; CR in beschrijvingen kan geen ICS-regels injecteren."""
from types import SimpleNamespace

import scheduler.agenda as agenda_mod


class _FakeSession:
    def __init__(self):
        self.timeouts = []

    def request(self, method, url, data=None, headers=None, timeout=None, **kwargs):
        self.timeouts.append(timeout)
        return SimpleNamespace(status_code=200, headers={}, content=b"{}")

    def close(self):
        pass


def test_google_refresh_request_defaults_to_15_seconds_not_120():
    session = _FakeSession()
    request = agenda_mod._timeout_request()
    request.session = session
    request("https://oauth2.googleapis.com/token", method="POST", body=b"x")
    assert session.timeouts == [agenda_mod._GOOGLE_HTTP_TIMEOUT_S] == [15]


def test_google_refresh_request_never_waits_longer_than_the_cap():
    session = _FakeSession()
    request = agenda_mod._timeout_request()
    request.session = session
    request("https://x", method="POST", body=b"x", timeout=300)          # een aanroeper vraagt meer: begrensd
    request("https://x", method="POST", body=b"x", timeout=5)            # korter mag wel
    assert session.timeouts == [15, 5]


def test_credentials_refresh_uses_the_capped_request(monkeypatch, tmp_path):
    token_path = tmp_path / "google_calendar_token.json"
    token_path.write_text('{"token": "t", "refresh_token": "rt"}', encoding="utf-8")
    monkeypatch.setattr(agenda_mod, "GOOGLE_TOKEN_FILE", token_path)
    seen = {}

    class FakeCreds:
        expired = True
        refresh_token = "rt"

        def refresh(self, request):
            session = _FakeSession()
            request.session = session
            request("https://oauth2.googleapis.com/token", method="POST", body=b"x")
            seen["timeouts"] = session.timeouts

        def to_json(self):
            return '{"token": "new", "refresh_token": "rt"}'

    import google.oauth2.credentials as gcred

    monkeypatch.setattr(gcred.Credentials, "from_authorized_user_file", lambda *a, **kw: FakeCreds())
    agenda_mod.GoogleCalendarAccount("a@b.c", "cid", "secret")._credentials()
    assert seen["timeouts"] == [15]


def test_a_carriage_return_in_a_description_cannot_inject_ics_properties():
    """splitlines() knipt ook op een los CR: "a<CR>SUMMARY:x" werd een tweede SUMMARY-regel."""
    item = {"summary": "Echte titel", "start": {"date": "2026-09-25"}, "end": {"date": "2026-09-26"},
            "description": "regel een\rSUMMARY:NEP\r\nLOCATION:elders\nDTSTART:19700101T000000"}
    event = agenda_mod._google_event_to_simple(item)
    props = dict(agenda_mod._vevent_props(event.data))
    assert props["SUMMARY"] == "Echte titel"
    assert "LOCATION" not in props                       # de geïnjecteerde regels zijn platte tekst gebleven
    assert props["DTSTART"] == "20260925"
    norm = agenda_mod._normalize_event(event, "Cal", "google")
    assert norm["title"] == "Echte titel" and "NEP" in norm["description"] and norm["all_day"] is True


def test_ics_escape_still_escapes_the_usual_suspects():
    assert agenda_mod._ics_escape("a,b;c") == "a\\,b\\;c"
    assert agenda_mod._ics_escape("een\ntwee") == "een\\ntwee"
    assert agenda_mod._ics_unescape(agenda_mod._ics_escape("x\r\ny,z;w\\")) == "x\ny,z;w\\"
