from datetime import datetime, timedelta, timezone

from caldav import DAVClient
from dateutil.parser import parse

import config

ICLOUD_URL = "https://caldav.icloud.com/"


class AppleCalendarMultiAccount:
    def __init__(self):
        pairs = (
            (config.secret("APPLE_ID_1"), config.secret("APPLE_PASSWORD_1")),
            (config.secret("APPLE_ID_2"), config.secret("APPLE_PASSWORD_2")),
        )
        self.accounts = [{"id": i, "password": p} for i, p in pairs if i and p]
        self.error = None
        self.calendars = self._connect()

    def _connect(self):
        cals = []
        errors = []
        for acc in self.accounts:
            try:
                client = DAVClient(ICLOUD_URL, username=acc["id"], password=acc["password"], timeout=15)
                cals.extend(client.principal().calendars() or [])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{acc['id']}: {exc}")
        if errors and not cals:
            self.error = "; ".join(errors)
        return cals

    # ------------------------------------------------------------------ #
    def get_all_events(self, start_date, end_date):
        events = []
        for cal in self.calendars:
            try:
                events.extend(cal.date_search(start_date, end_date))
            except Exception as exc:  # noqa: BLE001
                print(f"[agenda] fout bij '{getattr(cal, 'name', '?')}': {exc}")
        return events

    def get_todays_events(self):
        today = datetime.now().date()
        return self.get_all_events(today, today + timedelta(days=1))

    def get_tomorrows_events(self):
        d = datetime.now().date() + timedelta(days=1)
        return self.get_all_events(d, d + timedelta(days=1))

    def find_events_by_keyword(self, keyword, days_ahead=30):
        today = datetime.now().date()
        found = []
        for cal in self.calendars:
            try:
                for ev in cal.date_search(today, today + timedelta(days=days_ahead)):
                    if keyword.lower() in ev.data.lower():
                        found.append(ev)
            except Exception as exc:  # noqa: BLE001
                print(f"[agenda] zoekfout: {exc}")
        return found

    def add_event(self, summary, start, duration_hours=1):
        if not self.calendars:
            return "Geen agenda gevonden om event toe te voegen."
        end = start + timedelta(hours=duration_hours)
        template = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//kamerproject//NL\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{int(datetime.now().timestamp())}@kamerproject\r\n"
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}\r\n"
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}\r\n"
            f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}\r\n"
            f"SUMMARY:{summary}\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        try:
            self.calendars[0].add_event(template)
            return f"Event '{summary}' toegevoegd op {start.strftime('%Y-%m-%d %H:%M')}"
        except Exception as exc:  # noqa: BLE001
            return f"Kon event niet toevoegen: {exc}"

    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_event(event):
        summary, dtstart = "Geen titel", None
        for line in event.data.splitlines():
            if line.startswith("SUMMARY:"):
                summary = line[len("SUMMARY:"):].strip()
            elif line.startswith("DTSTART"):
                # 'DTSTART:...' of 'DTSTART;TZID=...:...' of 'DTSTART;VALUE=DATE:...'
                dtstart = line.split(":", 1)[1].strip() if ":" in line else None
        if not dtstart:
            return summary, "tijd onbekend"
        if "T" not in dtstart:
            return summary, "hele dag"
        try:
            return summary, parse(dtstart).strftime("%H:%M")
        except (ValueError, OverflowError):
            return summary, "tijd onbekend"

    def return_todays_events(self):
        events = self.get_todays_events()
        if not events:
            return ["Geen events vandaag!"]
        return [f"{s} om {t}" for s, t in (self._parse_event(e) for e in events)]

    def return_tomorrows_events(self):
        events = self.get_tomorrows_events()
        if not events:
            return ["Geen events morgen!"]
        return [f"{s} om {t}" for s, t in (self._parse_event(e) for e in events)]

    def next_event_with_keyword(self, keyword):
        events = self.find_events_by_keyword(keyword)
        if not events:
            return f"Geen '{keyword}' events gevonden in de komende 30 dagen."
        s, t = self._parse_event(events[0])
        return f"Je volgende '{s}' event is op: {t}"
