"""Lamps + thermostat."""
import asyncio
import re

from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S
from devices.Lights import describe_lamp_error
from Dashboard.backend.util import json_body

devices_bp = Blueprint("devices", __name__)


def _note_manual_lamp_action(ip: str) -> None:
    """Laat de aanwezigheidsautomatisering (presence.py) weten dat de lamp
    die 'ie zelf bedient net handmatig is aangestuurd -- puur voor
    zichtbaarheid (mode: MANUAL in /api/presence). De bestaande edge-
    triggered actuatie daar vecht toch al nooit meteen terug tegen een
    handmatige actie; dit maakt dat alleen expliciet zichtbaar."""
    try:
        if ip == S.lamp_ip(config.get("presence.lamp", 0)):
            from Dashboard.backend.presence import worker

            worker.note_manual_action()
    except Exception:  # noqa: BLE001 - mag een geslaagde handmatige actie nooit alsnog laten falen
        pass


def _bad_request(message: str):
    return jsonify({"status": "error", "message": message}), 400


_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _lamp_action(coro_factory, lamp_ref=None):
    if lamp_ref is None:
        lamp_ref = json_body().get("lamp", 0)
    ip = S.lamp_ip(lamp_ref)
    if not ip:
        return jsonify({"status": "error", "message": "geen lamp geconfigureerd"}), 400
    try:
        message = asyncio.run(coro_factory(S.lamp(ip)))
    except Exception as exc:  # noqa: BLE001
        S.drop_lamp(ip)
        return jsonify({"status": "error", "message": describe_lamp_error(exc)}), 500
    _note_manual_lamp_action(ip)
    return jsonify({"status": "ok", "message": message})


@devices_bp.route("/api/lamp/on", methods=["PUT"])
def lamp_on():
    return _lamp_action(lambda l: l.aan())


@devices_bp.route("/api/lamp/off", methods=["PUT"])
def lamp_off():
    return _lamp_action(lambda l: l.uit())


@devices_bp.route("/api/lamp/brightness", methods=["PUT"])
def lamp_brightness():
    # Ongeldige invoer is een 400, geen 500 + verbroken lampverbinding (de fout
    # kwam voorheen uit int() -> HTML-500; en een lamp-fout gooit de verbinding weg).
    try:
        b = int(json_body().get("brightness", 100))
    except (TypeError, ValueError):
        return _bad_request("helderheid moet een getal zijn (1-100)")
    b = max(1, min(100, b))          # de Tapo weigert 0 en >100
    return _lamp_action(lambda l: l.zet_helderheid(b))


@devices_bp.route("/api/lamp/color", methods=["PUT"])
def lamp_color():
    from devices.Lights import kleuren

    c = json_body().get("color")
    if not isinstance(c, str) or not (_HEX_RE.fullmatch(c) or c.lower() in kleuren):
        return _bad_request("kleur ontbreekt of is ongeldig (#rrggbb of een kleurnaam)")
    c = c if c.startswith("#") else c.lower()
    return _lamp_action(lambda l: l.zet_kleur(c))


@devices_bp.route("/api/lamp/colortemp", methods=["PUT"])
def lamp_colortemp():
    try:
        t = int(json_body().get("color_temp", 4000))
    except (TypeError, ValueError):
        return _bad_request("kleurtemperatuur moet een getal zijn (2500-6500)")
    t = max(2500, min(6500, t))      # bereik van de L530 (zelfde als de slider)
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
        return jsonify({"ok": False, "error": describe_lamp_error(exc)}), 503


@devices_bp.route("/api/lamps")
def lamps():
    return jsonify({"lamps": config.get("devices.lamps", [])})


@devices_bp.route("/api/thermostat", methods=["GET", "POST"])
def thermostat():
    from devices.thermostat import ThermostatController

    tc = ThermostatController()
    if request.method == "POST":
        target = json_body().get("target")
        if target is None:
            return jsonify({"success": False, "error": "geen target"}), 400
        try:
            tc.set_temperature(target)
        except (TypeError, ValueError, OverflowError):     # OverflowError: {"target": Infinity}
            return jsonify({"success": False, "error": "ongeldige waarde"}), 400
    return jsonify(tc.state())
