"""iCloud CalDAV agenda-integratie (scheduler/agenda.py).

Geen echte netwerkcalls hier -- DAVClient wordt gemockt. Zie het losse,
live connectietest-onderzoek (401 bij familiemaatman17@gmail.com) voor de
daadwerkelijke root-cause: dat bleek een ongeldig/ingetrokken Apple
app-specifiek wachtwoord voor dat ene account, niet een codefout. Deze
tests bewaken het gedrag dat wél is aangepast: een mislukt account mag niet
meer geruisloos verdwijnen achter een ander, wel werkend account.
"""


class _FakeCal:
    def __init__(self, name):
        self.name = name


class _FakePrincipal:
    def __init__(self, cals):
        self._cals = cals

    def calendars(self):
        return self._cals


class _FakeClient:
    """Simuleert caldav.DAVClient: succes of AuthorizationError per account,
    gestuurd op het meegegeven username."""

    def __init__(self, url, username=None, password=None, timeout=None, behavior=None):
        self.username = username
        self._behavior = behavior or {}

    def principal(self):
        outcome = self._behavior.get(self.username, "ok")
        if outcome == "ok":
            return _FakePrincipal([_FakeCal(f"Agenda van {self.username}")])
        from caldav.lib.error import AuthorizationError

        raise AuthorizationError(reason="Unauthorized", url="https://caldav.icloud.com/")


def _make_client_factory(behavior):
    def factory(url, username=None, password=None, timeout=None):
        return _FakeClient(url, username=username, password=password, timeout=timeout, behavior=behavior)
    return factory


def test_agenda_both_accounts_ok_no_error(monkeypatch):
    import scheduler.agenda as agenda_mod

    monkeypatch.setenv("APPLE_ID_1", "jason.maatman@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_1", "aaaa-bbbb-cccc-dddd")
    monkeypatch.setenv("APPLE_ID_2", "familiemaatman17@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_2", "eeee-ffff-gggg-hhhh")

    monkeypatch.setattr(agenda_mod, "DAVClient",
                         _make_client_factory({"jason.maatman@gmail.com": "ok",
                                                "familiemaatman17@gmail.com": "ok"}))

    cal = agenda_mod.AppleCalendarMultiAccount()
    assert cal.error is None
    assert len(cal.calendars) == 2


def test_agenda_partial_failure_still_returns_working_account(monkeypatch):
    """Kernfix: account 2 mislukt (net als de echte 401), account 1 werkt.
    Bestaande functionaliteit (agenda's van het werkende account) blijft
    gewoon beschikbaar -- maar de mislukking is niet langer onzichtbaar."""
    import scheduler.agenda as agenda_mod

    monkeypatch.setenv("APPLE_ID_1", "jason.maatman@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_1", "aaaa-bbbb-cccc-dddd")
    monkeypatch.setenv("APPLE_ID_2", "familiemaatman17@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_2", "eeee-ffff-gggg-hhhh")

    monkeypatch.setattr(agenda_mod, "DAVClient",
                         _make_client_factory({"jason.maatman@gmail.com": "ok",
                                                "familiemaatman17@gmail.com": "unauthorized"}))

    cal = agenda_mod.AppleCalendarMultiAccount()

    # bestaande functionaliteit: het werkende account levert gewoon zijn agenda
    assert len(cal.calendars) == 1
    assert cal.calendars[0].name == "Agenda van jason.maatman@gmail.com"

    # maar de mislukking van het andere account is nu zichtbaar...
    assert cal.error is not None
    assert "familiemaatman17@gmail.com" in cal.error
    assert "Unauthorized" in cal.error
    # ...en bevat nooit het wachtwoord
    assert "eeee-ffff-gggg-hhhh" not in cal.error


def test_agenda_both_accounts_fail_sets_error_and_no_calendars(monkeypatch):
    import scheduler.agenda as agenda_mod

    monkeypatch.setenv("APPLE_ID_1", "jason.maatman@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_1", "aaaa-bbbb-cccc-dddd")
    monkeypatch.setenv("APPLE_ID_2", "familiemaatman17@gmail.com")
    monkeypatch.setenv("APPLE_PASSWORD_2", "eeee-ffff-gggg-hhhh")

    monkeypatch.setattr(agenda_mod, "DAVClient",
                         _make_client_factory({"jason.maatman@gmail.com": "unauthorized",
                                                "familiemaatman17@gmail.com": "unauthorized"}))

    cal = agenda_mod.AppleCalendarMultiAccount()
    assert cal.calendars == []
    assert cal.error is not None
    assert "jason.maatman@gmail.com" in cal.error and "familiemaatman17@gmail.com" in cal.error


def test_agenda_partial_failure_surfaces_via_service_status(monkeypatch):
    """De health-check (/api/service_status) mag een gedeeltelijk mislukt
    account niet meer als 'ok' rapporteren."""
    from Dashboard.backend import services

    class FakeAgenda:
        error = "familiemaatman17@gmail.com: AuthorizationError at 'https://caldav.icloud.com/', reason Unauthorized"
        calendars = [object()]   # het werkende account heeft wel agenda's

    monkeypatch.setattr(services, "svc", lambda name: FakeAgenda() if name == "agenda" else None)
    ok, err = services._probe("agenda")
    assert ok is False
    assert "Unauthorized" in err
