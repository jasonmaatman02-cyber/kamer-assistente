"""ICS-parsing: gevouwen regels, geneste VALARM/VTIMEZONE, escapes, UTC -> lokale tijd."""
import datetime

import pytest

from scheduler import agenda as A


class Ev:
    def __init__(self, data):
        self.data = data


def _ics(*lines):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "\r\n".join(lines) + "\r\nEND:VCALENDAR\r\n"


def test_folded_lines_are_unfolded():
    loc = r"LOCATION:Rijksmuseum\, Museumstraat 1\, 1071 XX Amsterdam\, Netherlands"
    folded = loc[:40] + "\r\n " + loc[40:]
    data = _ics("BEGIN:VEVENT", "SUMMARY:Bezoek", "DTSTART:20260924T090000", folded, "END:VEVENT")
    ev = A._normalize_event(Ev(data), "Agenda", "icloud")
    assert ev["location"] == "Rijksmuseum, Museumstraat 1, 1071 XX Amsterdam, Netherlands"


def test_alarm_description_does_not_replace_the_event_description():
    data = _ics(
        "BEGIN:VEVENT", "SUMMARY:Tandarts", "DTSTART:20260924T090000",
        "DESCRIPTION:Neem je verzekeringspas mee",
        "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Herinnering", "TRIGGER:-PT15M", "END:VALARM",
        "END:VEVENT",
    )
    ev = A._normalize_event(Ev(data), "Agenda", "icloud")
    assert ev["description"] == "Neem je verzekeringspas mee"


def test_vtimezone_dtstart_does_not_leak_into_a_vevent_without_one():
    data = _ics(
        "BEGIN:VTIMEZONE", "TZID:Europe/Amsterdam", "BEGIN:STANDARD", "DTSTART:19701025T030000", "END:STANDARD", "END:VTIMEZONE",
        "BEGIN:VEVENT", "SUMMARY:Zonder start", "END:VEVENT",
    )
    ev = A._normalize_event(Ev(data), "Agenda", "icloud")
    assert ev["start"] is None
    assert A.MultiProviderCalendar._parse_event(Ev(data)) == ("Zonder start", "tijd onbekend")


def test_summary_with_escaped_characters_is_unescaped():
    """SUMMARY werd als enige veld niet ge-unescaped: een backslash-komma bleef zichtbaar."""
    data = _ics("BEGIN:VEVENT", r"SUMMARY:Lunch\, met Jan\; en Piet", "DTSTART:20260924T120000", "END:VEVENT")
    assert A._normalize_event(Ev(data), "A", "icloud")["title"] == "Lunch, met Jan; en Piet"
    assert A.MultiProviderCalendar._parse_event(Ev(data)) == ("Lunch, met Jan; en Piet", "12:00")


def test_summary_with_parameters_is_found():
    data = _ics("BEGIN:VEVENT", "SUMMARY;LANGUAGE=nl:Werkoverleg", "DTSTART;TZID=Europe/Amsterdam:20260924T140000", "END:VEVENT")
    assert A.MultiProviderCalendar._parse_event(Ev(data)) == ("Werkoverleg", "14:00")


def test_utc_start_is_announced_in_local_time(monkeypatch):
    """'DTSTART:...Z' werd als UTC-klokslag voorgelezen ('om 07:00' voor een afspraak om 09:00)."""
    monkeypatch.setattr(A, "_local_tz", lambda: datetime.timezone(datetime.timedelta(hours=2)))
    data = _ics("BEGIN:VEVENT", "SUMMARY:Call", "DTSTART:20260924T070000Z", "END:VEVENT")
    assert A.MultiProviderCalendar._parse_event(Ev(data)) == ("Call", "09:00")
    floating = _ics("BEGIN:VEVENT", "SUMMARY:Call", "DTSTART:20260924T070000", "END:VEVENT")
    assert A.MultiProviderCalendar._parse_event(Ev(floating)) == ("Call", "07:00")     # zwevend = ongewijzigd


def test_all_day_event():
    data = _ics("BEGIN:VEVENT", "SUMMARY:Verjaardag", "DTSTART;VALUE=DATE:20260924", "END:VEVENT")
    assert A.MultiProviderCalendar._parse_event(Ev(data)) == ("Verjaardag", "hele dag")
    assert A._normalize_event(Ev(data), "A", "icloud")["all_day"] is True


def test_bare_property_block_without_vevent_still_parses():
    assert A._normalize_event(Ev("SUMMARY:Kaal\r\nDTSTART:20260924T090000\r\n"), "A", "x")["title"] == "Kaal"


def test_google_summary_roundtrips_special_characters_and_cannot_inject_properties():
    item = {"summary": "Lunch, met Jan\nLOCATION:evil", "start": {"dateTime": "2026-09-24T12:00:00+02:00"},
            "end": {"dateTime": "2026-09-24T13:00:00+02:00"}, "id": "abc"}
    simple = A._google_event_to_simple(item)
    ev = A._normalize_event(simple, "G", "google")
    assert ev["title"] == "Lunch, met Jan\nLOCATION:evil"
    assert ev["location"] == ""                       # niet als eigenschap geïnjecteerd


def test_add_event_escapes_summary_and_uses_unique_uids():
    class Cal:
        def __init__(self):
            self.added = []

        def add_event(self, template):
            self.added.append(template)

    multi = object.__new__(A.MultiProviderCalendar)
    cal = Cal()
    multi.calendars = [cal]
    start = datetime.datetime(2026, 9, 24, 10, 0)
    multi.add_event("Titel\r\nATTENDEE:mailto:x@y.z", start)
    multi.add_event("Tweede", start)
    first, second = cal.added
    assert "\r\nATTENDEE:" not in first
    uids = [next(l for l in t.split("\r\n") if l.startswith("UID:")) for t in (first, second)]
    assert uids[0] != uids[1]
