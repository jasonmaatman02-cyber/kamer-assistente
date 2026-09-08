"""System / settings / overview endpoints."""
from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({"settings": config.all_settings(), "defaults": config.DEFAULTS, "errors": S.errors()})


@system_bp.route("/api/settings", methods=["POST"])
def set_settings():
    patch = request.get_json(silent=True) or {}
    if not isinstance(patch, dict):
        return jsonify({"success": False, "error": "verwacht een JSON-object"}), 400
    config.update(patch)
    S.reset_services()
    return jsonify({"success": True, "settings": config.all_settings()})


@system_bp.route("/api/config")
def public_config():
    return jsonify({
        "poll": config.get("dashboard"),
        "camera": {
            "browser_detection": config.get("camera.browser_detection"),
            "enabled": config.get("camera.enabled"),
        },
        "features": config.get("features"),
    })


@system_bp.route("/api/system_stats")
def system_stats():
    data = S.system_stats()
    return jsonify(data), (500 if "error" in data else 200)


@system_bp.route("/api/service_status")
def service_status():
    return jsonify(S.service_status())


@system_bp.route("/api/weather")
def weather():
    data = S.weather_data(request.args.get("city"))
    return jsonify(data), (503 if data.get("error") else 200)


@system_bp.route("/api/calendar_today")
def calendar_today():
    return jsonify(S.calendar_today())


@system_bp.route("/api/overview")
def overview():
    from logic.notes import list_all_notes

    return jsonify({
        "system": S.system_stats(),
        "services": S.service_status(),
        "weather": S.weather_data(),
        "calendar": S.calendar_today(),
        "now_playing": S.current_playing(),
        "notes": list_all_notes(),
    })


@system_bp.route("/api/notifications")
def notifications():
    from datetime import datetime
    from pathlib import Path

    limit = int(request.args.get("limit", 40))
    base = Path(__file__).resolve().parent.parent.parent / "data" / "logs"
    lines: list[str] = []
    if base.is_dir():
        for fp in sorted(base.rglob("*.txt"))[-4:]:
            try:
                lines.extend(fp.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass
    parsed = []
    for ln in lines[-limit:][::-1]:
        ln = ln.strip()
        if not ln:
            continue
        subject, message, tijd = "", ln, ""
        if ln.startswith("[") and "]" in ln:
            try:
                tijd = ln[1:ln.index("]")]
                rest = ln[ln.index("]") + 1:].strip()
                if rest.startswith("[") and "]" in rest:
                    subject = rest[1:rest.index("]")]
                    message = rest[rest.index("]") + 1:].strip()
                else:
                    message = rest
            except ValueError:
                pass
        parsed.append({"time": tijd, "subject": subject, "message": message})
    return jsonify({"notifications": parsed, "date": datetime.now().strftime("%Y-%m-%d")})
