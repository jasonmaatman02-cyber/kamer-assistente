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
