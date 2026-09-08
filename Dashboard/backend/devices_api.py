"""Lamps + thermostat."""
import asyncio

from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S

devices_bp = Blueprint("devices", __name__)


def _lamp_action(coro_factory, lamp_ref=None):
    if lamp_ref is None:
        lamp_ref = (request.get_json(silent=True) or {}).get("lamp", 0)
    ip = S.lamp_ip(lamp_ref)
    if not ip:
        return jsonify({"status": "error", "message": "geen lamp geconfigureerd"}), 400
    try:
        return jsonify({"status": "ok", "message": asyncio.run(coro_factory(S.lamp(ip)))})
    except Exception as exc:  # noqa: BLE001
        S.drop_lamp(ip)
        return jsonify({"status": "error", "message": str(exc)}), 500


@devices_bp.route("/api/lamp/on", methods=["PUT"])
def lamp_on():
    return _lamp_action(lambda l: l.aan())


@devices_bp.route("/api/lamp/off", methods=["PUT"])
def lamp_off():
    return _lamp_action(lambda l: l.uit())


@devices_bp.route("/api/lamp/brightness", methods=["PUT"])
def lamp_brightness():
    b = int((request.get_json(silent=True) or {}).get("brightness", 100))
    return _lamp_action(lambda l: l.zet_helderheid(b))


@devices_bp.route("/api/lamp/color", methods=["PUT"])
def lamp_color():
    c = (request.get_json(silent=True) or {}).get("color")
    return _lamp_action(lambda l: l.zet_kleur(c))


@devices_bp.route("/api/lamp/colortemp", methods=["PUT"])
def lamp_colortemp():
    t = int((request.get_json(silent=True) or {}).get("color_temp", 4000))
    return _lamp_action(lambda l: l.zet_kleur_temp(t))


@devices_bp.route("/api/lamp/mode/<mode>", methods=["POST"])
def lamp_mode(mode):
    factory = {
        "party": lambda l: l.party(duur=10, interval=0.2),
        "desk": lambda l: l.bureau(),
        "normal": lambda l: l.normaal(),
    }.get(mode)
    if not factory:
        return jsonify({"status": "error", "message": "onbekende modus"}), 400
    return _lamp_action(factory)


@devices_bp.route("/api/lamp/state")
def lamp_state():
    ip = S.lamp_ip(request.args.get("lamp", 0))
    if not ip:
        return jsonify({"ok": False, "error": "geen lamp"}), 400
    try:
        return jsonify({"ok": True, "state": asyncio.run(S.lamp(ip).status())})
    except Exception as exc:  # noqa: BLE001
        S.drop_lamp(ip)
        return jsonify({"ok": False, "error": str(exc)}), 503


@devices_bp.route("/api/lamps")
def lamps():
    return jsonify({"lamps": config.get("devices.lamps", [])})


@devices_bp.route("/api/thermostat", methods=["GET", "POST"])
def thermostat():
    from devices.thermostat import ThermostatController

    tc = ThermostatController()
    if request.method == "POST":
        target = (request.get_json(silent=True) or {}).get("target")
        if target is None:
            return jsonify({"success": False, "error": "geen target"}), 400
        try:
            tc.set_temperature(target)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "ongeldige waarde"}), 400
    return jsonify(tc.state())
