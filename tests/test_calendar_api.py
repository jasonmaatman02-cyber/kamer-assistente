"""Kalender-tab: /api/calendar/events + de normalisatielaag in scheduler/agenda.py.

De frontend (calendar.js) praat uitsluitend met deze route en het platte
event-formaat dat scheduler.agenda._normalize_event teruggeeft -- geen
provider-specifieke logica in de frontend, dus deze tests bewaken vooral dat
beide providers (Google, iCloud) op precies dezelfde manier genormaliseerd
worden.
"""
from datetime import date


class _FakeCal:
    def __init__(self, name, events, provider):
        self.name = name
        self._events = events
        self.provider = provider

    def date_search(self, start, end):
        return self._events


def _icloud_event(summary, dtstart, dtend=None, location=None, description=None, uid=None):
    import scheduler.agenda as agenda_mod

    lines = [f"SUMMARY:{summary}", f"DTSTART:{dtstart}"]
    if dtend:
        lines.append(f"DTEND:{dtend}")
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if uid:
        lines.append(f"UID:{uid}")
    data = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    return agenda_mod._SimpleEvent(data)


# --------------------------------------------------------------------------- #
# 1. Google events worden correct weergegeven / genormaliseerd
# --------------------------------------------------------------------------- #
def test_google_event_normalized(monkeypatch, amsterdam_summer):
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [_FakeCal("Persoonlijk", [
        agenda_mod._google_event_to_simple({
            "id": "g-evt-1",
            "summary": "Tandarts",
            "start": {"dateTime": "2026-09-20T09:00:00+02:00"},
            "end": {"dateTime": "2026-09-20T09:30:00+02:00"},
            "location": "Hoofdstraat 1",
            "description": "Controle",
        }),
    ], "google")]

    out = cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30))
    assert len(out) == 1
    ev = out[0]
    assert ev["id"] == "g-evt-1"
    assert ev["title"] == "Tandarts"
    assert ev["provider"] == "google"
    assert ev["calendar"] == "Persoonlijk"
    assert ev["location"] == "Hoofdstraat 1"
    assert ev["description"] == "Controle"
    assert ev["all_day"] is False
    assert ev["start"].startswith("2026-09-20T09:00:00")
    assert ev["end"].startswith("2026-09-20T09:30:00")


def test_google_all_day_event_normalized():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [_FakeCal("Feestdagen", [
        agenda_mod._google_event_to_simple({
            "summary": "Koningsdag", "start": {"date": "2026-04-27"}, "end": {"date": "2026-04-28"},
        }),
    ], "google")]

    out = cal.get_normalized_events(date(2026, 4, 1), date(2026, 4, 30))
    assert len(out) == 1
    assert out[0]["all_day"] is True
    assert out[0]["title"] == "Koningsdag"


# --------------------------------------------------------------------------- #
# 2. iCloud events worden correct weergegeven / genormaliseerd
# --------------------------------------------------------------------------- #
def test_icloud_event_normalized():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [_FakeCal("Werk", [
        _icloud_event("Teamoverleg", "20260920T140000", "20260920T150000",
                       location="Kantoor", description="Sprint review", uid="uid-123"),
    ], "icloud")]

    out = cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30))
    assert len(out) == 1
    ev = out[0]
    assert ev["id"] == "uid-123"
    assert ev["title"] == "Teamoverleg"
    assert ev["provider"] == "icloud"
    assert ev["calendar"] == "Werk"
    assert ev["location"] == "Kantoor"
    assert ev["description"] == "Sprint review"
    assert ev["all_day"] is False


def test_icloud_all_day_event_normalized():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [_FakeCal("Verjaardagen", [
        _icloud_event("Verjaardag Jason", "20260614"),
    ], "icloud")]

    out = cal.get_normalized_events(date(2026, 6, 1), date(2026, 6, 30))
    assert out[0]["all_day"] is True


# --------------------------------------------------------------------------- #
# 3. Events worden correct genormaliseerd -- zelfde vorm, ongeacht provider
# --------------------------------------------------------------------------- #
def test_both_providers_produce_identical_shape():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [
        _FakeCal("Google-agenda", [agenda_mod._google_event_to_simple(
            {"summary": "G", "start": {"dateTime": "2026-09-20T09:00:00+02:00"}})], "google"),
        _FakeCal("iCloud-agenda", [_icloud_event("I", "20260920T100000")], "icloud"),
    ]
    out = cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30))
    assert len(out) == 2
    expected_keys = {"id", "title", "start", "end", "all_day", "calendar", "provider", "location", "description"}
    for ev in out:
        assert set(ev.keys()) == expected_keys


# --------------------------------------------------------------------------- #
# 4. Lege kalender werkt
# --------------------------------------------------------------------------- #
def test_empty_calendar_returns_empty_list():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [_FakeCal("Leeg", [], "icloud")]
    out = cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30))
    assert out == []


def test_no_calendars_at_all_returns_empty_list():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = []
    assert cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30)) == []


# --------------------------------------------------------------------------- #
# 5. API-fout wordt netjes afgehandeld (één kapotte agenda breekt de rest niet)
# --------------------------------------------------------------------------- #
def test_one_broken_calendar_does_not_break_the_others():
    import scheduler.agenda as agenda_mod

    class BrokenCal:
        name = "Kapot"
        provider = "google"

        def date_search(self, start, end):
            raise RuntimeError("Google API: quota exceeded")

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [
        BrokenCal(),
        _FakeCal("Werkt wel", [_icloud_event("OK", "20260920T090000")], "icloud"),
    ]
    out = cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30))
    assert len(out) == 1
    assert out[0]["title"] == "OK"


def test_route_reports_agenda_unavailable(client, monkeypatch):
    from Dashboard.backend import services as S

    monkeypatch.setattr(S, "svc", lambda name: None)
    monkeypatch.setitem(S._errors, "agenda", "geen accounts geconfigureerd")

    r = client.get("/api/calendar/events?start=2026-09-01&end=2026-09-30")
    body = r.get_json()
    assert body["events"] == []
    assert "geen accounts" in body["error"]


def test_route_surfaces_partial_provider_error(client, monkeypatch):
    from Dashboard.backend import services as S

    class FakeCal:
        error = "familiemaatman17@gmail.com: RuntimeError('niet gekoppeld')"
        calendars = []

        def get_normalized_events(self, start, end):
            return []

    monkeypatch.setattr(S, "svc", lambda name: FakeCal() if name == "agenda" else None)
    r = client.get("/api/calendar/events?start=2026-09-01&end=2026-09-30")
    body = r.get_json()
    assert body["events"] == []
    assert "familiemaatman17@gmail.com" in body["error"]


# --------------------------------------------------------------------------- #
# 6. Event-details werken (alle velden voor de modal aanwezig)
# --------------------------------------------------------------------------- #
def test_event_has_all_fields_needed_for_details_modal():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [_FakeCal("Werk", [
        _icloud_event("Sprint demo", "20260920T140000", "20260920T150000",
                       location="Vergaderzaal A", description="Toon de kalender-tab", uid="uid-9"),
    ], "icloud")]

    ev = cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30))[0]
    # precies de velden die de modal (titel/datum/begin/eind/agenda/locatie/beschrijving) nodig heeft
    assert ev["title"] == "Sprint demo"
    assert ev["start"] and ev["end"]
    assert ev["calendar"] == "Werk"
    assert ev["location"] == "Vergaderzaal A"
    assert ev["description"] == "Toon de kalender-tab"


def test_event_without_location_or_description_has_empty_strings():
    import scheduler.agenda as agenda_mod

    cal = agenda_mod.MultiProviderCalendar()
    cal.calendars = [_FakeCal("Werk", [_icloud_event("Kaal event", "20260920T090000")], "icloud")]
    ev = cal.get_normalized_events(date(2026, 9, 1), date(2026, 9, 30))[0]
    assert ev["location"] == ""
    assert ev["description"] == ""


def test_events_route_is_cached_per_range(client, monkeypatch):
    """Zonder caching deed elke navigatie-klik in calendar.js (geen eigen
    debounce/inflight-guard) een verse, live aanroep naar Google/iCloud --
    kon bij snel doorbladeren veel onnodige aanroepen achter elkaar
    triggeren. Binnen hetzelfde bereik moet get_normalized_events() maar
    één keer draaien; een ANDER bereik moet wel een eigen, verse aanroep
    krijgen."""
    calls = {"n": 0}

    class FakeCal:
        error = None
        calendars = [object()]

        def get_normalized_events(self, start, end):
            calls["n"] += 1
            return [{"id": f"call-{calls['n']}"}]

    from Dashboard.backend import services as S
    fake = FakeCal()
    monkeypatch.setattr(S, "svc", lambda name: fake if name == "agenda" else None)

    r1 = client.get("/api/calendar/events?start=2026-09-01&end=2026-09-30").get_json()
    r2 = client.get("/api/calendar/events?start=2026-09-01&end=2026-09-30").get_json()
    assert r1 == r2 and calls["n"] == 1          # zelfde bereik -> uit cache

    r3 = client.get("/api/calendar/events?start=2026-10-01&end=2026-10-31").get_json()
    assert calls["n"] == 2                        # ander bereik -> verse aanroep
    assert r3["events"][0]["id"] == "call-2"


def test_route_end_to_end_returns_normalized_events(client, monkeypatch):
    import scheduler.agenda as agenda_mod
    from Dashboard.backend import services as S

    fake = agenda_mod.MultiProviderCalendar.__new__(agenda_mod.MultiProviderCalendar)
    fake.error = None
    fake.calendars = [_FakeCal("Werk", [_icloud_event("Route-test", "20260920T090000")], "icloud")]
    monkeypatch.setattr(S, "svc", lambda name: fake if name == "agenda" else None)

    r = client.get("/api/calendar/events?start=2026-09-01&end=2026-09-30")
    assert r.status_code == 200
    body = r.get_json()
    assert body["error"] is None
    assert len(body["events"]) == 1
    assert body["events"][0]["title"] == "Route-test"


def test_calendar_tab_explains_when_no_account_is_linked(client, monkeypatch):
    """Zonder gekoppelde agenda gaf de Kalender-tab een lege kalender zonder
    enige uitleg (cal.error was None)."""
    from Dashboard.backend import services as S

    class FakeCal:
        error = None
        calendars = []

        def get_normalized_events(self, start, end):
            return []

    monkeypatch.setattr(S, "svc", lambda name: FakeCal() if name == "agenda" else None)
    body = client.get("/api/calendar/events?start=2026-09-01&end=2026-09-30").get_json()
    assert body["events"] == [] and "Geen agenda gekoppeld" in body["error"]
