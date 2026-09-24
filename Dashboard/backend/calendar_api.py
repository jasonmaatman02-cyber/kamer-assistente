"""Kalender-tab: provider-onafhankelijke agenda-events voor de dashboard-UI.

De frontend praat uitsluitend met /api/calendar/events en krijgt een plat,
uniform event-formaat terug (zie scheduler.agenda._normalize_event) -- geen
Google- of iCloud-specifieke logica hier of in calendar.js.
"""
from datetime import date, datetime, timedelta

from dateutil.parser import parse
from flask import Blueprint, jsonify, request

from Dashboard.backend import services as S
from Dashboard.backend.auth import require_password

calendar_bp = Blueprint("calendar", __name__)


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return parse(value).date()
    except (ValueError, OverflowError):
        return default


@calendar_bp.route("/api/calendar/events")
@require_password       # de Kalender-pagina zit achter het wachtwoord; de data-API hoort daar niet open naast te staan
def calendar_events():
    today = datetime.now().date()
    start = _parse_date(request.args.get("start"), today)
    end = _parse_date(request.args.get("end"), start + timedelta(days=1))
    return jsonify(S.calendar_events(start, end))
