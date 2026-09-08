"""Flask app for the Smart Home dashboard.

Assembled from blueprints (see the sibling ``*_api.py`` modules). Nothing
hardware/network-y happens at import time — every external service is created
lazily in :mod:`Dashboard.backend.services` and wrapped so one failure degrades
a single card instead of the whole dashboard.
"""
from flask import Flask

app = Flask(__name__, template_folder="../", static_folder="../static")

from Dashboard.backend.auth import auth_bp
from Dashboard.backend.camera_api import camera_bp
from Dashboard.backend.devices_api import devices_bp
from Dashboard.backend.media_api import media_bp
from Dashboard.backend.pages import pages_bp
from Dashboard.backend.routines_api import routines_bp
from Dashboard.backend.secrets_api import secrets_bp
from Dashboard.backend.system_api import system_bp

for bp in (auth_bp, system_bp, secrets_bp, media_bp, devices_bp,
           camera_bp, routines_bp, pages_bp):
    app.register_blueprint(bp)

# backwards-compat re-exports
from Dashboard.backend.services import reset_services, svc  # noqa: E402,F401
