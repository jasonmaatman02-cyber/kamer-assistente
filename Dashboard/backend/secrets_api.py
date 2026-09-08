"""Secrets editor + Spotify OAuth helper. Gated by the OTP unlock."""
import re

from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S
from Dashboard.backend.auth import mail_ready, unlocked

secrets_bp = Blueprint("secrets", __name__)


def _validate(key: str, value: str) -> str | None:
    """Return an error string, or None if ok. Empty values are allowed (= unset)."""
    v = value.strip()
    if not v:
        return None
    if key == "SMTP_PORT" and not v.isdigit():
        return "moet een getal zijn"
    if key in ("EMAIL_ADDRESS", "RECEIVER", "TAPO_USER", "APPLE_ID_1", "APPLE_ID_2") and "@" not in v:
        return "verwacht een e-mailadres"
    if key.endswith("_URI") and not re.match(r"^https?://", v):
        return "verwacht een http(s)-URL"
    return None


@secrets_bp.route("/api/secrets", methods=["GET"])
def get_secrets():
    return jsonify({
        "secrets": config.secret_status(),
        "keys": config.SECRET_KEYS,
        "unlocked": unlocked(),
        "mail_ready": mail_ready(),
    })


@secrets_bp.route("/api/secrets", methods=["POST"])
def set_secrets():
    if not unlocked():
        return jsonify({"ok": False, "error": "Niet ontgrendeld"}), 403
    data = request.get_json(silent=True) or {}
    for key, value in data.items():
        if key in config.SECRET_KEYS and isinstance(value, str):
            err = _validate(key, value)
            if err:
                return jsonify({"ok": False, "error": f"{key}: {err}"}), 400
    changed = []
    for key, value in data.items():
        if key in config.SECRET_KEYS and isinstance(value, str):
            try:
                config.set_secret(key, value)
                changed.append(key)
            except Exception as exc:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"{key}: {exc}"}), 500
    config.reload()
    S.reset_services()
    return jsonify({"ok": True, "changed": changed})


@secrets_bp.route("/api/spotify/auth-url")
def spotify_auth_url():
    if not unlocked():
        return jsonify({"ok": False, "error": "Niet ontgrendeld"}), 403
    oauth = S.spotify_oauth()
    if not oauth:
        return jsonify({"ok": False, "error": "Vul eerst SPOTIFY_CLIENT_ID en SPOTIFY_CLIENT_SECRET in"}), 400
    return jsonify({"ok": True, "url": oauth.get_authorize_url()})


@secrets_bp.route("/api/spotify/token", methods=["POST"])
def spotify_token():
    if not unlocked():
        return jsonify({"ok": False, "error": "Niet ontgrendeld"}), 403
    oauth = S.spotify_oauth()
    if not oauth:
        return jsonify({"ok": False, "error": "Spotify client-id/secret ontbreekt"}), 400
    redirect_url = (request.get_json(silent=True) or {}).get("redirect_url", "").strip()
    if not redirect_url:
        return jsonify({"ok": False, "error": "Plak de volledige URL waar je op uitkwam"}), 400
    try:
        code = oauth.parse_response_code(redirect_url)
        oauth.get_access_token(code, as_dict=False, check_cache=False)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400
    S.reset_services()
    return jsonify({"ok": True})
