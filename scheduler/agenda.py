"""Agenda: meerdere accounts, elk met een eigen provider.

Account 1 is altijd iCloud/CalDAV (bestaand, ongewijzigd gedrag). Account 2
is expliciet provider-specifiek (``agenda.account2_provider`` in Settings) --
niet langer automatisch aangenomen dat elk tweede e-mailadres een Apple ID
is, want dat hoeft niet zo te zijn (bv. een gewoon Google-account).

De rest van Kamer-AI (scheduler/gpt_handler, dashboard) gebruikt uitsluitend
MultiProviderCalendar's provider-onafhankelijke interface
(get_*_events / return_*_events / add_event) en hoeft nooit te weten welke
provider een account daadwerkelijk gebruikt:

    Google Calendar / iCloud CalDAV
            v
    OAuth credentials / app-wachtwoord
            v
    token storage (google_calendar_token.json) / -
            v
    Calendar API / CalDAV
            v
    zelfde interne event-formaat (_SimpleEvent, .data = ruwe VEVENT-tekst)
            v
    Kamer-AI scheduler/dashboard

Elke provider levert alleen 'calendar'-achtige objecten met .name en
.date_search(start, end) -> list[event]; elk event heeft alleen .data (de
ruwe iCalendar VEVENT-tekst) nodig -- dat is precies wat caldav's eigen
Calendar/Event-objecten al bieden, dus de bestaande _parse_event()-logica
werkt ongewijzigd voor beide providers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from caldav import DAVClient
from dateutil.parser import parse

import config

ICLOUD_URL = "https://caldav.icloud.com/"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
GOOGLE_TOKEN_FILE = Path(__file__).resolve().parent.parent / "google_calendar_token.json"


# --------------------------------------------------------------------------- #
# Provider-onafhankelijk event-formaat
# --------------------------------------------------------------------------- #
class _SimpleEvent:
    """Duck-typed als een caldav Event: alleen ``.data`` (ruwe iCalendar
    VEVENT-tekst) wordt door ``_parse_event``/``find_events_by_keyword``
    gelezen. Zo hoeft de rest van Kamer-AI nooit te weten welke provider
    een event daadwerkelijk levert."""

    def __init__(self, data: str):
        self.data = data


def _ics_vevent(summary: str, dtstart_line: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//kamerproject//NL\r\n"
        "BEGIN:VEVENT\r\n"
        f"SUMMARY:{summary}\r\n"
        f"{dtstart_line}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def _google_event_to_simple(item: dict) -> _SimpleEvent:
    summary = item.get("summary") or "Geen titel"
    start = item.get("start", {})
    if "dateTime" in start:
        dt = parse(start["dateTime"])
        dtstart_line = f"DTSTART:{dt.strftime('%Y%m%dT%H%M%S')}"
    else:
        day = (start.get("date") or "").replace("-", "")
        dtstart_line = f"DTSTART;VALUE=DATE:{day}"
    return _SimpleEvent(_ics_vevent(summary, dtstart_line))


def _rfc3339_day_start(d) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Providers -- elk levert alleen 'calendar'-achtige objecten (.name +
# .date_search(start, end)); de orchestrator hieronder weet niet welke
# provider het is.
# --------------------------------------------------------------------------- #
class ICloudCalDAVAccount:
    """Eén iCloud-account via CalDAV (app-specifiek wachtwoord). Zelfde
    verbindingslogica als voorheen, nu herbruikbaar per account i.p.v.
    inline in de orchestrator."""

    provider = "icloud"

    def __init__(self, email: str, app_password: str):
        self.email = email
        self._password = app_password

    def calendars(self):
        client = DAVClient(ICLOUD_URL, username=self.email, password=self._password, timeout=15)
        return client.principal().calendars() or []


class _GoogleCalendarView:
    """Duck-typed als een caldav Calendar: alleen ``.name`` en
    ``.date_search(start, end)`` worden elders gebruikt."""

    def __init__(self, service, calendar_id: str, name: str):
        self._service = service
        self._id = calendar_id
        self.name = name

    def date_search(self, start, end):
        resp = self._service.events().list(
            calendarId=self._id,
            timeMin=_rfc3339_day_start(start),
            timeMax=_rfc3339_day_start(end),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute()
        return [_google_event_to_simple(item) for item in resp.get("items", [])]


class GoogleCalendarAccount:
    """Eén Google-account via de officiële Google Calendar API + OAuth 2.0.

    Het token wordt persistent opgeslagen in ``GOOGLE_TOKEN_FILE`` (net als
    Spotify's ``.cache``) zodat er na de eerste, eenmalige browser-
    autorisatie (Settings -> Google Agenda -> Verbind) nooit meer opnieuw
    ingelogd hoeft te worden: een verlopen access token wordt automatisch
    ververst met de refresh token, geen herstart of nieuwe login nodig.
    De Google-imports zijn bewust lokaal (niet bovenaan dit bestand) zodat
    een ontbrekende google-*-library nooit het hele agenda-bestand (en dus
    scheduler.routines / de wekker) onimporteerbaar maakt."""

    provider = "google"

    def __init__(self, email: str, client_id: str, client_secret: str):
        self.email = email
        self._client_id = client_id
        self._client_secret = client_secret

    def _credentials(self):
        # Module-attribuut (niet als default-argument gebonden) zodat tests
        # 'm kunnen monkeypatchen naar een tijdelijk pad -- een default-
        # argumentwaarde wordt anders al bij het importeren van dit bestand
        # vastgezet en pikt een latere monkeypatch niet meer op.
        token_path = GOOGLE_TOKEN_FILE
        if not token_path.exists():
            raise RuntimeError(
                "Google Agenda nog niet gekoppeld -- zie Settings -> Google Agenda -> Verbind met Google"
            )
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def calendars(self):
        from googleapiclient.discovery import build

        creds = self._credentials()
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        items = service.calendarList().list().execute().get("items", [])
        return [_GoogleCalendarView(service, c["id"], c.get("summary") or self.email) for c in items]


def _build_account(id_key: str, password_key: str, provider_config_key: str, default_provider: str):
    """Bouwt het account voor één slot (1 of 2), met een expliciet instelbare
    provider per slot -- niet langer aangenomen dat slot 1 per definitie
    iCloud is. Zo maakt het niet uit welk e-mailadres er in welk
    APPLE_ID_N-veld staat; de provider bepaalt het gedrag, niet de positie."""
    provider = (config.get(provider_config_key, default_provider) or "").strip().lower()
    email = config.secret(id_key)
    if provider == "icloud":
        password = config.secret(password_key)
        if email and password:
            return ICloudCalDAVAccount(email, password)
    elif provider == "google":
        cid, csecret = config.secret("GOOGLE_CLIENT_ID"), config.secret("GOOGLE_CLIENT_SECRET")
        if email and cid and csecret:
            return GoogleCalendarAccount(email, cid, csecret)
    return None


def _configured_accounts() -> list:
    accounts = []
    for acc in (
        _build_account("APPLE_ID_1", "APPLE_PASSWORD_1", "agenda.account1_provider", "icloud"),
        _build_account("APPLE_ID_2", "APPLE_PASSWORD_2", "agenda.account2_provider", "google"),
    ):
        if acc is not None:
            accounts.append(acc)
    return accounts


# --------------------------------------------------------------------------- #
# Orchestrator: verzamelt agenda's van alle geconfigureerde accounts,
# provider-onafhankelijk.
# --------------------------------------------------------------------------- #
class MultiProviderCalendar:
    def __init__(self):
        self._accounts = _configured_accounts()
        self.error = None
        self.calendars = self._connect()

    def _connect(self):
        cals = []
        errors = []
        for acc in self._accounts:
            try:
                cals.extend(acc.calendars())
            except Exception as exc:  # noqa: BLE001 - de foutmelding zelf bevat
                # nooit het wachtwoord/token (caldav's AuthorizationError geeft
                # alleen url+reason; onze eigen RuntimeErrors bevatten geen
                # secrets), dus veilig om te loggen/tonen.
                errors.append(f"{acc.email}: {exc}")
        # Ook zichtbaar maken als één account faalt terwijl een ander wel lukt --
        # anders verdwijnt een kapot account geruisloos zodra er nog een
        # werkend account is (agenda leek dan "ok").
        if errors:
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
        """Voegt toe aan de eerste beschikbare agenda. CalDAV (iCloud)
        ondersteunt dit rechtstreeks; het toevoegen van events aan Google
        Agenda is niet gevraagd en niet geïmplementeerd -- staat een
        Google-agenda toevallig vooraan, dan degradeert dit netjes naar de
        bestaande foutafhandeling hieronder (geen crash)."""
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


AppleCalendarMultiAccount = MultiProviderCalendar   # backward-compat alias
