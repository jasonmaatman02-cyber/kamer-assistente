"""Two independent gates:

* **OTP unlock** — a 6-digit code e-mailed to RECEIVER, unlocks the Secrets
  editor for 30 minutes (header ``X-Unlock-Token``).
* **Password gate** — an optional dashboard password (``DASHBOARD_PASSWORD``
  secret). When set, the ``@require_password`` decorator protects the sensitive
  pages/endpoints (camera, chat, routines, settings) with a 30-day session
  cookie. When not set, the gate is a no-op so a fresh install stays open.
"""
from __future__ import annotations

import functools
import secrets as _secrets
import time

from flask import Blueprint, jsonify, render_template, request

import config

auth_bp = Blueprint("auth", __name__)

# --------------------------------------------------------------------------- #
# OTP unlock (secrets editor)
# --------------------------------------------------------------------------- #
_otp = {"code": None, "expires": 0.0, "fails": 0}
_unlock_sessions: dict[str, float] = {}
_UNLOCK_TTL = 30 * 60
_OTP_TTL = 10 * 60
_OTP_MAX_FAILS = 5


def mail_ready() -> bool:
    return bool(
        config.secret("EMAIL_ADDRESS")
        and config.secret("EMAIL_PASSWORD")
        and config.secret("RECEIVER")
    )


def unlocked() -> bool:
    token = request.headers.get("X-Unlock-Token", "")
    exp = _unlock_sessions.get(token)
    if exp and time.time() < exp:
        return True
    _unlock_sessions.pop(token, None)
    return False


@auth_bp.route("/api/auth/request-code", methods=["POST"])
def request_code():
    if not mail_ready():
        return jsonify({
            "ok": False,
            "error": "Mail nog niet ingesteld. Vul EMAIL_ADDRESS, EMAIL_PASSWORD en "
                     "RECEIVER eenmalig in via het .env-bestand op de Pi.",
        }), 400
    from logic.mail_sender import send_email_message

    code = f"{_secrets.randbelow(1_000_000):06d}"
    _otp.update(code=code, expires=time.time() + _OTP_TTL, fails=0)
    result = send_email_message(
        "Kamer-assistent: ontgrendelcode",
        f"Je code om de instellingen te ontgrendelen: {code}\n\n"
        f"Verloopt over 10 minuten. Niet aangevraagd? Negeer deze mail.",
    )
    if "mislukt" in result.lower():
        return jsonify({"ok": False, "error": "Kon de mail niet versturen — check de Gmail-gegevens."}), 502
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/verify", methods=["POST"])
def verify():
    if _otp["fails"] >= _OTP_MAX_FAILS:
        _otp.update(code=None, expires=0.0)
        return jsonify({"ok": False, "error": "Te veel pogingen. Vraag een nieuwe code aan."}), 429
    code = (request.get_json(silent=True) or {}).get("code", "").strip()
    if not _otp["code"] or time.time() > _otp["expires"] or code != _otp["code"]:
        _otp["fails"] += 1
        return jsonify({"ok": False, "error": "Code ongeldig of verlopen"}), 401
    _otp.update(code=None, expires=0.0, fails=0)
    token = _secrets.token_urlsafe(24)
    _unlock_sessions[token] = time.time() + _UNLOCK_TTL
    return jsonify({"ok": True, "token": token, "ttl": _UNLOCK_TTL})


# --------------------------------------------------------------------------- #
# Password gate (whole sensitive sections)
# --------------------------------------------------------------------------- #
COOKIE = "kamer_session"
_pw_sessions: dict[str, float] = {}
_PW_TTL = 30 * 24 * 3600
_pw_fails = {"n": 0, "until": 0.0}
_PW_MAX_FAILS = 5
_PW_LOCK = 60


def password_set() -> bool:
    return bool(config.secret("DASHBOARD_PASSWORD"))


def logged_in() -> bool:
    if not password_set():
        return True
    tok = request.cookies.get(COOKIE, "")
    exp = _pw_sessions.get(tok)
    if exp and time.time() < exp:
        return True
    _pw_sessions.pop(tok, None)
    return False


def require_password(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if logged_in():
            return fn(*a, **kw)
        if request.path.startswith("/api/") or request.path == "/video_feed":
            return jsonify({"error": "login vereist", "login_required": True}), 401
        return render_template("login.html")
    return wrapper


@auth_bp.route("/api/login", methods=["POST"])
def login():
    now = time.time()
    if _pw_fails["n"] >= _PW_MAX_FAILS and now < _pw_fails["until"]:
        return jsonify({"ok": False, "error": "Te veel pogingen, wacht even."}), 429
    given = (request.get_json(silent=True) or {}).get("password", "")
    want = config.secret("DASHBOARD_PASSWORD")
    if not want:
        return jsonify({"ok": True, "note": "geen wachtwoord ingesteld"})
    if not _secrets.compare_digest(str(given), str(want)):
        _pw_fails["n"] += 1
        _pw_fails["until"] = now + _PW_LOCK
        return jsonify({"ok": False, "error": "Onjuist wachtwoord"}), 401
    _pw_fails.update(n=0, until=0.0)
    tok = _secrets.token_urlsafe(32)
    _pw_sessions[tok] = now + _PW_TTL
    resp = jsonify({"ok": True})
    resp.set_cookie(COOKIE, tok, max_age=_PW_TTL, httponly=True, samesite="Lax")
    return resp


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    _pw_sessions.pop(request.cookies.get(COOKIE, ""), None)
    resp = jsonify({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@auth_bp.route("/api/auth/status")
def status():
    return jsonify({"password_required": password_set(), "logged_in": logged_in()})
