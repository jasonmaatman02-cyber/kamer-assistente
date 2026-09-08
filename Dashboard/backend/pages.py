"""HTML page routes."""
from flask import Blueprint, Response, render_template

from Dashboard.backend.auth import require_password

pages_bp = Blueprint("pages", __name__)

_PAGES = {
    "main": "main.html",
    "devices": "devices.html",
    "media": "media.html",
    "environment": "environment.html",
    "routines": "routines.html",
    "notes": "notes.html",
    "chat": "chat.html",
    "notifications": "notifications.html",
    "camera": "camera.html",
    "settings": "settings.html",
}
# pages achter het dashboard-wachtwoord (indien gezet)
_PROTECTED = {"camera", "chat", "routines", "settings"}


@pages_bp.route("/favicon.ico")
def favicon():
    return Response(status=204)


@pages_bp.route("/")
@pages_bp.route("/<page>")
def page(page: str = "main"):
    template = _PAGES.get(page)
    if not template:
        return render_template("main.html", active="main"), 404
    if page in _PROTECTED:
        return _protected_page(template, page)
    return render_template(template, active=page)


@require_password
def _protected_page(template, page):
    return render_template(template, active=page)
