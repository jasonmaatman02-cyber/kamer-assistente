"""Flask app for the Smart Home dashboard.

Assembled from blueprints (see the sibling ``*_api.py`` modules). Nothing
hardware/network-y happens at import time — every external service is created
lazily in :mod:`Dashboard.backend.services` and wrapped so one failure degrades
a single card instead of the whole dashboard.
"""
from urllib.parse import urlparse

from flask import Flask, request

app = Flask(__name__, template_folder="../", static_folder="../static")


@app.before_request
def _csrf_guard():
    """Blokkeer state-changing requests van een andere site (CSRF). Same-origin
    browserverzoeken sturen een matchende Origin; curl/scripts sturen er geen.
    Sec-Fetch-Site (moderne browsers) is leidend; anders Origin vs Host."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sfs = request.headers.get("Sec-Fetch-Site")
    if sfs in ("same-origin", "same-site", "none"):
        return
    origin = request.headers.get("Origin")
    same = origin and urlparse(origin).netloc == request.host
    if sfs == "cross-site" or (origin and not same):
        print(f"[csrf] geweigerd: {request.method} {request.path} "
              f"origin={origin!r} host={request.host!r} sec-fetch-site={sfs!r}")
        return ("cross-site verzoek geweigerd", 403)

from Dashboard.backend.auth import auth_bp
from Dashboard.backend.camera_api import camera_bp
from Dashboard.backend.chat_api import chat_bp
from Dashboard.backend.devices_api import devices_bp
from Dashboard.backend.media_api import media_bp
from Dashboard.backend.pages import pages_bp
from Dashboard.backend.routines_api import routines_bp
from Dashboard.backend.secrets_api import secrets_bp
from Dashboard.backend.system_api import system_bp

for bp in (auth_bp, system_bp, secrets_bp, media_bp, devices_bp,
           camera_bp, chat_bp, routines_bp, pages_bp):
    app.register_blueprint(bp)

# backwards-compat re-exports
from Dashboard.backend.services import reset_services, svc  # noqa: E402,F401
