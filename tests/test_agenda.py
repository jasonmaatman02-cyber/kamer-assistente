"""Agenda: meerdere providers (iCloud CalDAV + Google Calendar) naast elkaar.

Geen echte netwerk-/OAuth-calls hier: DAVClient wordt gemockt voor iCloud,
en GoogleCalendarAccount.calendars() wordt direct gemockt (of, voor de
"nog niet gekoppeld"-scenario's, gewoon écht aangeroepen -- die faalt dan
op eigen kracht doordat het token-bestand niet bestaat, precies zoals in
productie).

Achtergrond: familiemaatman17@gmail.com bleek helemaal geen iCloud-account
te zijn maar een gewoon Google-account -- vandaar dat account 2 nu een
losstaande, expliciet gekozen provider heeft i.p.v. automatisch CalDAV.
"""
import pytest
from datetime import date


class _FakeCal:
    def __init__(self, name, events=None):
        self.name = name
        self._events = events or []

    def date_search(self, start, end):
        return self._events


class _FakePrincipal:
    def __init__(self, cals):
        self._cals = cals

    def calendars(self):
        return self._cals


class _FakeICloudClient:
    """Simuleert caldav.DAVClient: succes of AuthorizationError per account,
    gestuurd op het meegegeven username."""

    def __init__(self, url, username=None, password=None, timeout=None, behavior=None, events=None):
        self.username = username
        self._behavior = behavior or {}
        self._events = events or {}

    def principal(self):
        outcome = self._behavior.get(self.username, "ok")
        if outcome == "ok":
            evs = self._events.get(self.username, [])
            return _FakePrincipal([_FakeCal(f"Agenda van {self.username}", evs)])
        from caldav.lib.error import AuthorizationError
        raise AuthorizationError(reason="Unauthorized", url="https://caldav.icloud.com/")


def _icloud_factory(behavior, events=None):
    def factory(url, username=None, password=None, timeout=None):
        return _FakeICloudClient(url, username=username, password=password, timeout=timeout,
                                  behavior=behavior, events=events)
    return factory


def _setup_icloud1(monkeypatch, agenda_mod, outcome="ok", events=None):
    monkeypatch.setenv("APPLE_ID_1", "jason.maatman@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_1", "aaaa-bbbb-cccc-dddd")
    monkeypatch.setattr(agenda_mod, "DAVClient",
                         _icloud_factory({"jason.maatman@gmail.com": outcome}, events))


def _setup_google2(monkeypatch, agenda_mod, calendars_fn=None):
    monkeypatch.setenv("APPLE_ID_2", "familiemaatman17@gmail.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gcid-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gcsecret-verygeheim")
    import config
    config.set("agenda.account2_provider", "google")
    if calendars_fn is not None:
        monkeypatch.setattr(agenda_mod.GoogleCalendarAccount, "calendars", calendars_fn)


# --------------------------------------------------------------------------- #
# 1. iCloud-account succesvol
# --------------------------------------------------------------------------- #
def test_icloud_account_succeeds(monkeypatch):
    import scheduler.agenda as agenda_mod

    events = {"jason.maatman@gmail.com": [agenda_mod._SimpleEvent(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Tandarts\r\nDTSTART:20260920T090000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )]}
    _setup_icloud1(monkeypatch, agenda_mod, "ok", events)

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.error is None
    assert len(cal.calendars) == 1
    fetched = cal.get_all_events(date(2026, 9, 1), date(2026, 9, 30))
    assert len(fetched) == 1


# --------------------------------------------------------------------------- #
# 2. iCloud-account faalt
# --------------------------------------------------------------------------- #
def test_icloud_account_fails(monkeypatch):
    import scheduler.agenda as agenda_mod

    _setup_icloud1(monkeypatch, agenda_mod, "unauthorized")

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.calendars == []
    assert cal.error is not None
    assert "jason.maatman@gmail.com" in cal.error
    assert "Unauthorized" in cal.error


# --------------------------------------------------------------------------- #
# 3. Google-account succesvol
# --------------------------------------------------------------------------- #
def test_google_account_succeeds(monkeypatch):
    import scheduler.agenda as agenda_mod

    def fake_calendars(self):
        return [_FakeCal("Persoonlijk (Google)",
                          [agenda_mod._google_event_to_simple(
                              {"summary": "Verjaardag", "start": {"date": "2026-09-20"}})])]

    _setup_google2(monkeypatch, agenda_mod, fake_calendars)

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.error is None
    assert len(cal.calendars) == 1
    fetched = cal.get_all_events(date(2026, 9, 1), date(2026, 9, 30))
    assert len(fetched) == 1


# --------------------------------------------------------------------------- #
# 4. Google-authenticatie/API faalt
# --------------------------------------------------------------------------- #
def test_google_account_not_linked_fails_cleanly(monkeypatch):
    """Geen calendars_fn meegegeven -> echte GoogleCalendarAccount._credentials()
    loopt, vindt geen tokenbestand (conftest wijst GOOGLE_TOKEN_FILE naar een
    niet-bestaand tmp-pad) en faalt netjes, precies zoals in productie
    vóórdat er ooit gekoppeld is."""
    import scheduler.agenda as agenda_mod

    _setup_google2(monkeypatch, agenda_mod)   # geen calendars_fn -> echte (falende) code

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.calendars == []
    assert cal.error is not None
    assert "familiemaatman17@gmail.com" in cal.error
    assert "niet gekoppeld" in cal.error


def test_google_account_api_error_fails_cleanly(monkeypatch):
    """Ook een fout ná succesvolle authenticatie (bv. een echte API-fout)
    crasht de worker niet en wordt zichtbaar."""
    import scheduler.agenda as agenda_mod

    def boom(self):
        raise RuntimeError("Google Calendar API: quota exceeded")

    _setup_google2(monkeypatch, agenda_mod, boom)

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.calendars == []
    assert cal.error is not None
    assert "quota exceeded" in cal.error


# --------------------------------------------------------------------------- #
# 5. Eén provider werkt terwijl de andere faalt (beide richtingen)
# --------------------------------------------------------------------------- #
def test_icloud_ok_google_fails(monkeypatch):
    import scheduler.agenda as agenda_mod

    events = {"jason.maatman@gmail.com": [agenda_mod._SimpleEvent(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Werk\r\nDTSTART:20260920T090000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )]}
    _setup_icloud1(monkeypatch, agenda_mod, "ok", events)
    _setup_google2(monkeypatch, agenda_mod)   # niet gekoppeld -> faalt

    cal = agenda_mod.MultiProviderCalendar()
    assert len(cal.calendars) == 1                       # iCloud blijft werken
    assert cal.calendars[0].name == "Agenda van jason.maatman@gmail.com"
    assert cal.error is not None and "familiemaatman17@gmail.com" in cal.error


def test_google_ok_icloud_fails(monkeypatch):
    import scheduler.agenda as agenda_mod

    _setup_icloud1(monkeypatch, agenda_mod, "unauthorized")

    def fake_calendars(self):
        return [_FakeCal("Persoonlijk (Google)", [])]
    _setup_google2(monkeypatch, agenda_mod, fake_calendars)

    cal = agenda_mod.MultiProviderCalendar()
    assert len(cal.calendars) == 1                        # Google blijft werken
    assert cal.calendars[0].name == "Persoonlijk (Google)"
    assert cal.error is not None and "jason.maatman@gmail.com" in cal.error


# --------------------------------------------------------------------------- #
# 6. Beide providers werken
# --------------------------------------------------------------------------- #
def test_both_providers_succeed(monkeypatch):
    import scheduler.agenda as agenda_mod

    _setup_icloud1(monkeypatch, agenda_mod, "ok", {"jason.maatman@gmail.com": []})

    def fake_calendars(self):
        return [_FakeCal("Persoonlijk (Google)", [])]
    _setup_google2(monkeypatch, agenda_mod, fake_calendars)

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.error is None
    assert len(cal.calendars) == 2
    names = {c.name for c in cal.calendars}
    assert names == {"Agenda van jason.maatman@gmail.com", "Persoonlijk (Google)"}


# --------------------------------------------------------------------------- #
# 7. Credentials/secrets worden nooit gelogd
# --------------------------------------------------------------------------- #
def test_secrets_never_appear_in_error(monkeypatch):
    import scheduler.agenda as agenda_mod

    SECRET_PW = "aaaa-bbbb-cccc-super-geheim"
    SECRET_CLIENT_SECRET = "gcsecret-super-geheim-xyz"

    monkeypatch.setenv("APPLE_ID_1", "jason.maatman@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_1", SECRET_PW)
    monkeypatch.setattr(agenda_mod, "DAVClient",
                         _icloud_factory({"jason.maatman@gmail.com": "unauthorized"}))

    monkeypatch.setenv("APPLE_ID_2", "familiemaatman17@gmail.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gcid-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", SECRET_CLIENT_SECRET)
    import config
    config.set("agenda.account2_provider", "google")

    monkeypatch.setattr(agenda_mod.GoogleCalendarAccount, "calendars",
                         lambda self: (_ for _ in ()).throw(RuntimeError("Google API-fout: token ongeldig")))

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.error is not None
    assert SECRET_PW not in cal.error
    assert SECRET_CLIENT_SECRET not in cal.error
    assert "gcid-123" not in cal.error   # ook de client-id hoeft er niet in te staan


# --------------------------------------------------------------------------- #
# 8. Events van beide providers hebben hetzelfde interne formaat
# --------------------------------------------------------------------------- #
def test_events_have_uniform_internal_format(monkeypatch):
    import scheduler.agenda as agenda_mod

    icloud_events = [agenda_mod._SimpleEvent(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:iCloud-afspraak\r\n"
        "DTSTART:20260920T090000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )]
    _setup_icloud1(monkeypatch, agenda_mod, "ok", {"jason.maatman@gmail.com": icloud_events})

    def fake_calendars(self):
        google_event = agenda_mod._google_event_to_simple({
            "summary": "Google-afspraak",
            "start": {"dateTime": "2026-09-20T14:00:00+02:00"},
        })
        return [_FakeCal("Google", [google_event])]
    _setup_google2(monkeypatch, agenda_mod, fake_calendars)

    cal = agenda_mod.MultiProviderCalendar()
    all_events = cal.get_all_events(date(2026, 9, 1), date(2026, 9, 30))
    assert len(all_events) == 2
    for ev in all_events:
        assert hasattr(ev, "data") and isinstance(ev.data, str)   # zelfde interface

    parsed = {agenda_mod.MultiProviderCalendar._parse_event(e) for e in all_events}
    assert parsed == {("iCloud-afspraak", "09:00"), ("Google-afspraak", "14:00")}


def test_all_day_google_event_parses_as_hele_dag(monkeypatch):
    import scheduler.agenda as agenda_mod

    ev = agenda_mod._google_event_to_simple({"summary": "Vrije dag", "start": {"date": "2026-09-20"}})
    summary, when = agenda_mod.MultiProviderCalendar._parse_event(ev)
    assert summary == "Vrije dag"
    assert when == "hele dag"


# --------------------------------------------------------------------------- #
# Health/status: gedeeltelijke mislukking blijft zichtbaar (bestaande fix)
# --------------------------------------------------------------------------- #
def test_partial_failure_surfaces_via_service_status(monkeypatch):
    from Dashboard.backend import services

    class FakeAgenda:
        error = "familiemaatman17@gmail.com: RuntimeError('Google Agenda nog niet gekoppeld')"
        calendars = [object()]

    monkeypatch.setattr(services, "svc", lambda name: FakeAgenda() if name == "agenda" else None)
    ok, err = services._probe("agenda")
    assert ok is False
    assert "familiemaatman17@gmail.com" in err


def test_provider_follows_config_not_slot_position(monkeypatch):
    """Regressie: echt live gebeurd -- de accounts stonden per ongeluk
    omgewisseld in Settings (slot 1 had het Google-adres, slot 2 het
    iCloud-adres). De provider hoort te volgen uit agenda.account1_provider/
    account2_provider, niet uit de aanname 'slot 1 = altijd iCloud'."""
    import config
    import scheduler.agenda as agenda_mod

    # slot 1 = Google (dus GEEN CalDAV-poging naar caldav.icloud.com voor
    # dit adres); slot 2 = iCloud, moet gewoon via CalDAV blijven werken.
    monkeypatch.setenv("APPLE_ID_1", "familiemaatman17@gmail.com")
    config.set("agenda.account1_provider", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gcid-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gcsecret")
    monkeypatch.setattr(agenda_mod.GoogleCalendarAccount, "calendars",
                         lambda self: [_FakeCal("Google (slot 1)", [])])

    monkeypatch.setenv("APPLE_ID_2", "jason.maatman@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_2", "aaaa-bbbb-cccc-dddd")
    config.set("agenda.account2_provider", "icloud")
    monkeypatch.setattr(agenda_mod, "DAVClient",
                         _icloud_factory({"jason.maatman@gmail.com": "ok",
                                           "familiemaatman17@gmail.com": "should_never_be_asked"}))

    cal = agenda_mod.MultiProviderCalendar()
    assert cal.error is None
    names = {c.name for c in cal.calendars}
    assert "Google (slot 1)" in names
    assert "Agenda van jason.maatman@gmail.com" in names


def test_backward_compat_alias_still_importable():
    from scheduler.agenda import AppleCalendarMultiAccount, MultiProviderCalendar
    assert AppleCalendarMultiAccount is MultiProviderCalendar


# --------------------------------------------------------------------------- #
# 9. Google Calendar HTTP-timeout (ontbrak eerder, zelfde risico als de
#    eerder gevonden ontbrekende SpotifyOAuth-timeout)
# --------------------------------------------------------------------------- #
def test_google_calendar_http_transport_has_a_timeout(monkeypatch):
    """build(credentials=...) bouwt zelf een httplib2.Http() ZONDER timeout --
    een onbereikbare/trage Google-API zou dan een waitress-workerthread voor
    altijd kunnen bezet houden (elke agenda-aanroep gaat hierlangs). Controleert
    dat calendars() nu zelf een timeout-bound AuthorizedHttp opbouwt en die aan
    build() doorgeeft i.p.v. credentials= rechtstreeks."""
    import scheduler.agenda as agenda_mod

    account = agenda_mod.GoogleCalendarAccount("test@example.com", "cid", "csecret")
    monkeypatch.setattr(account, "_credentials", lambda: object())

    captured = {}

    class FakeService:
        def calendarList(self):
            class _L:
                def list(self_inner):
                    class _E:
                        def execute(self_inner2, **kw):
                            return {"items": []}
                    return _E()
            return _L()

    def fake_build(serviceName, version, http=None, credentials=None, cache_discovery=True):
        assert credentials is None, "http= en credentials= mogen niet allebei aan build()"
        captured["http"] = http
        return FakeService()

    # calendars() doet 'from googleapiclient.discovery import build' LOKAAL
    # (bewust, zie de docstring van GoogleCalendarAccount) -- monkeypatchen
    # moet dus op de module zelf, niet op scheduler.agenda.
    import googleapiclient.discovery
    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)

    account.calendars()

    http = captured.get("http")
    assert http is not None, "build() had een http= transport moeten krijgen"
    assert http.http.timeout == 15


# --------------------------------------------------------------------------- #
# 10. google_calendar_token.json wordt atomisch weggeschreven bij elke
#     tokenverversing (niet alleen bij de eenmalige koppeling) -- zelfde
#     risico als de eerder gevonden niet-atomische settings.json-write.
# --------------------------------------------------------------------------- #
def test_google_token_refresh_writes_atomically(monkeypatch, tmp_path):
    import scheduler.agenda as agenda_mod

    token_path = tmp_path / "google_calendar_token.json"
    original = '{"token": "old-token", "refresh_token": "rt"}'
    token_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(agenda_mod, "GOOGLE_TOKEN_FILE", token_path)

    account = agenda_mod.GoogleCalendarAccount("test@example.com", "cid", "csecret")

    class FakeCreds:
        expired = True
        refresh_token = "rt"

        def refresh(self, request):
            pass

        def to_json(self):
            return '{"token": "new-token", "refresh_token": "rt"}'

    import google.oauth2.credentials as gcred
    monkeypatch.setattr(gcred.Credentials, "from_authorized_user_file", lambda *a, **kw: FakeCreds())

    from pathlib import Path
    real_write_text = Path.write_text

    def boom_write_text(self, *a, **kw):
        if self == token_path.with_suffix(".json.tmp"):
            raise OSError("gesimuleerde crash tijdens schrijven")
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", boom_write_text)

    import pytest
    with pytest.raises(OSError):
        account._credentials()

    assert token_path.read_text(encoding="utf-8") == original, \
        "een mislukte tokenverversing mag het bestaande token-bestand niet aanraken"
    assert not token_path.with_suffix(".json.tmp").exists()


def test_expired_google_token_gives_an_actionable_message():
    from scheduler.agenda import MultiProviderCalendar, describe_calendar_error

    raw = ("('invalid_grant: Token has been expired or revoked.', "
           "{'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'})")
    msg = describe_calendar_error(RuntimeError(raw))
    assert "koppel opnieuw" in msg and "7 dagen" in msg
    assert "invalid_grant" not in msg
    assert describe_calendar_error(RuntimeError("iets anders")) == "iets anders"

    class Broken:
        email = "a@b.c"
        provider = "google"

        def calendars(self):
            raise RuntimeError(raw)

    cal = object.__new__(MultiProviderCalendar)
    cal._accounts = [Broken()]
    cal.error = None
    cal._connect()
    assert "koppel opnieuw" in cal.error and cal.error.startswith("a@b.c:")


# --------------------------------------------------------------------------- #
# Agenda die bij het opstarten faalt herstelt zichzelf
# --------------------------------------------------------------------------- #
class _FlakyCal:
    name = "Prive"

    def date_search(self, start, end):
        class E:
            data = "BEGIN:VEVENT\r\nSUMMARY:Tandarts\r\nDTSTART:20260924T090000\r\nEND:VEVENT\r\n"
        return [E()]


class _FlakyAccount:
    provider = "google"

    def __init__(self, email, fail_times):
        self.email = email
        self.fail_times = fail_times
        self.calls = 0

    def calendars(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("Temporary failure in name resolution")
        return [_FlakyCal()]


def _multi(accounts):
    import scheduler.agenda as A

    cal = object.__new__(A.MultiProviderCalendar)
    cal._accounts = accounts
    cal.error = None
    cal.calendars = cal._connect()
    return cal


def test_calendar_that_failed_at_startup_is_retried_and_recovers():
    """De verbinding werd maar EEN keer gemaakt (bij het opstarten): was het netwerk toen even weg,
    dan bleef de agenda kapot tot een herstart."""
    acc = _FlakyAccount("a@b.c", fail_times=1)
    cal = _multi([acc])
    assert cal.calendars == [] and "a@b.c" in cal.error and acc.calls == 1

    assert cal.get_all_events(None, None) == [] and acc.calls == 1      # binnen de wachttijd: geen nieuwe poging

    cal._last_attempt -= 61                                             # een minuut later
    events = cal.get_all_events(None, None)
    assert acc.calls == 2 and len(events) == 1
    assert cal.error is None and len(cal.calendars) == 1

    cal.get_all_events(None, None)                                      # verbonden: geen extra pogingen meer
    assert acc.calls == 2


def test_retry_only_touches_the_failed_account_and_keeps_the_error_for_it():
    ok, bad = _FlakyAccount("goed@b.c", 0), _FlakyAccount("kapot@b.c", 99)
    cal = _multi([ok, bad])
    assert len(cal.calendars) == 1 and "kapot@b.c" in cal.error

    cal._last_attempt -= 61
    cal.get_all_events(None, None)
    assert ok.calls == 1                                                # het werkende account niet opnieuw verbonden
    assert bad.calls == 2 and len(cal.calendars) == 1
    assert "kapot@b.c" in cal.error and "goed@b.c" not in cal.error


def test_retries_are_rate_limited_and_never_concurrent():
    import threading

    acc = _FlakyAccount("a@b.c", fail_times=99)
    cal = _multi([acc])
    cal._last_attempt -= 61
    threads = [threading.Thread(target=cal.get_all_events, args=(None, None)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert acc.calls == 2                                               # 1x opstart + 1x retry, niet 8x
    cal.get_all_events(None, None)
    assert acc.calls == 2                                               # en de volgende minuut weer pas


# --------------------------------------------------------------------------- #
# Google-hardening: lokale dagen, tijdzones, kapotte items, fouten zichtbaar
# --------------------------------------------------------------------------- #
def test_day_boundaries_are_local_midnight_not_utc(monkeypatch):
    """timeMin/timeMax waren UTC-middernacht: op de Pi (UTC+2) viel een event van 00:30 lokaal onder de
    vorige dag en verscheen het van morgen (00:30) bij 'vandaag'."""
    import datetime

    import scheduler.agenda as A

    monkeypatch.setattr(A, "_local_tz", lambda: datetime.timezone(datetime.timedelta(hours=2)))
    assert A._rfc3339_day_start(datetime.date(2026, 9, 24)) == "2026-09-24T00:00:00+02:00"
    monkeypatch.setattr(A, "_local_tz", lambda: datetime.timezone(datetime.timedelta(hours=-5)))
    assert A._rfc3339_day_start(datetime.date(2026, 9, 24)) == "2026-09-24T00:00:00-05:00"


def test_google_datetime_with_an_offset_is_converted_to_local_time(monkeypatch):
    import datetime

    import scheduler.agenda as A

    monkeypatch.setattr(A, "_local_tz", lambda: datetime.timezone(datetime.timedelta(hours=2)))
    # event in New York (-04:00) om 09:00 = 15:00 in Nederland (+02:00)
    item = {"summary": "Call", "start": {"dateTime": "2026-09-24T09:00:00-04:00"},
            "end": {"dateTime": "2026-09-24T10:00:00-04:00"}}
    ev = A._normalize_event(A._google_event_to_simple(item), "G", "google")
    assert ev["start"].startswith("2026-09-24T15:00:00") and ev["end"].startswith("2026-09-24T16:00:00")
    utc = {"summary": "Z", "start": {"dateTime": "2026-09-24T07:00:00Z"}}
    assert A._normalize_event(A._google_event_to_simple(utc), "G", "google")["start"].startswith("2026-09-24T09:00:00")


@pytest.mark.parametrize("item", [
    {},
    {"summary": None, "start": None, "end": None},
    {"summary": 5, "start": {"dateTime": "geen-datum"}},
    {"summary": "x", "start": "2026-09-24"},
    {"summary": "x", "start": {"date": None}, "location": 12, "description": ["a"]},
])
def test_malformed_google_items_never_crash_the_conversion(item):
    import scheduler.agenda as A

    ev = A._normalize_event(A._google_event_to_simple(item), "G", "google")
    assert ev["title"] and isinstance(ev["all_day"], bool)


def test_one_malformed_event_does_not_hide_the_rest_of_the_calendar(capsys):
    import scheduler.agenda as A

    class Svc:
        def events(self):
            return self

        def list(self, **kw):
            self.kw = kw
            return self

        def execute(self, **kw):
            self.retries = kw.get("num_retries")
            return {"items": [{"summary": "goed", "start": {"date": "2026-09-24"}}, {"boom": object()},
                              None, {"summary": "ook goed", "start": {"date": "2026-09-25"}}]}

    import datetime
    svc = Svc()
    view = A._GoogleCalendarView(svc, "id", "Agenda")
    events = view.date_search(datetime.date(2026, 9, 24), datetime.date(2026, 9, 26))
    titles = [A._normalize_event(e, "A", "google")["title"] for e in events]
    assert titles == ["goed", "Geen titel", "ook goed"]      # het lege item blijft, het kapotte item (None) valt weg
    assert svc.retries == 2                                  # tijdelijke Google-fouten/rate limits: eigen retries
    assert "T00:00:00" in svc.kw["timeMin"] and "T00:00:00" in svc.kw["timeMax"]


def test_null_items_from_google_are_treated_as_empty():
    import datetime

    import scheduler.agenda as A

    class Svc:
        def events(self):
            return self

        def list(self, **kw):
            return self

        def execute(self, **kw):
            return {"items": None}

    assert A._GoogleCalendarView(Svc(), "i", "n").date_search(datetime.date(2026, 9, 24), datetime.date(2026, 9, 25)) == []


def test_calendar_that_fails_to_fetch_is_reported_not_shown_as_no_events(monkeypatch):
    """Een ingetrokken Google-token tijdens de draai gaf 'Geen events vandaag!' (schijnbaar succes)."""
    from Dashboard.backend import services as S
    from scheduler.agenda import MultiProviderCalendar

    class Cal:
        name = "Prive"

        def date_search(self, start, end):
            raise RuntimeError("('invalid_grant: Token has been expired or revoked.', {})")

    cal = object.__new__(MultiProviderCalendar)
    cal._accounts, cal.error, cal._failed, cal._last_attempt, cal.fetch_errors = [], None, [], 0.0, []
    cal.calendars = [Cal()]
    S._services["agenda"] = cal
    today = S.calendar_today(fresh=True)
    assert today["events"] == [] and "koppel opnieuw" in today["error"]
    rng = S.calendar_events(__import__("datetime").date(2026, 9, 24), __import__("datetime").date(2026, 9, 25))
    assert "koppel opnieuw" in rng["error"] and rng["events"] == []

    class Good(Cal):
        def date_search(self, start, end):
            class E:
                data = "BEGIN:VEVENT\r\nSUMMARY:Tandarts\r\nDTSTART:20260924T090000\r\nEND:VEVENT\r\n"
            return [E()]

    cal.calendars = [Good()]
    S._data_cache.clear()
    ok = S.calendar_today(fresh=True)
    assert ok.get("error") is None and any("Tandarts" in e for e in ok["events"])
