"""Routines, notes and the manual dashboard alarm."""
import asyncio

from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S
from Dashboard.backend.auth import require_password
from logic.logger import log
from scheduler.alarm import AlarmScheduler

routines_bp = Blueprint("routines", __name__)

_BUILTIN = [
    {"id": "morning", "name": "Ochtend-routine", "desc": "Lampen aan, radio, notities voorlezen", "builtin": True},
    {"id": "bedtime", "name": "Bedtijd-routine", "desc": "Lampen uit, welterusten, wekker zetten", "builtin": True},
    {"id": "party", "name": "Party-modus", "desc": "Flitsende kleuren op de lampen", "builtin": True},
    {"id": "desk", "name": "Bureau-stand", "desc": "Zacht warm licht om te werken", "builtin": True},
]


@routines_bp.route("/api/routines")
def routines_list():
    custom = config.get("routines", []) or []
    return jsonify({"routines": _BUILTIN + [{**r, "builtin": False} for r in custom]})


@routines_bp.route("/api/routines", methods=["POST"])
@require_password
def routines_save():
    data = request.get_json(silent=True) or {}
    rid = str(data.get("id", "")).strip()
    name = str(data.get("name", "")).strip()
    if not rid or not name:
        return jsonify({"success": False, "error": "id en naam vereist"}), 400
    if rid in {r["id"] for r in _BUILTIN}:
        return jsonify({"success": False, "error": "ingebouwde routine-id"}), 400
    steps = data.get("steps") or []
    if not isinstance(steps, list):
        return jsonify({"success": False, "error": "steps moet een lijst zijn"}), 400
    custom = [r for r in (config.get("routines", []) or []) if r.get("id") != rid]
    custom.append({"id": rid, "name": name, "desc": data.get("desc", ""), "steps": steps})
    config.set("routines", custom)
    return jsonify({"success": True})


@routines_bp.route("/api/routines/<rid>", methods=["DELETE"])
@require_password
def routines_delete(rid):
    custom = [r for r in (config.get("routines", []) or []) if r.get("id") != rid]
    config.set("routines", custom)
    return jsonify({"success": True})


def _run_step(step):
    kind = step.get("action")
    if kind == "lamp":
        ip = S.lamp_ip(step.get("lamp", 0))
        lamp = S.lamp(ip)
        mode = step.get("mode", "on")
        coro = {"on": lamp.aan, "off": lamp.uit, "desk": lamp.bureau,
                "party": lamp.party, "normal": lamp.normaal}.get(mode, lamp.aan)
        asyncio.run(coro())
    elif kind == "radio":
        r = S.svc("radio")
        if r:
            r.play(step.get("station", "radio538"))
    elif kind == "spotify":
        dj = S.svc("spotify")
        if dj and step.get("playlist_id"):
            dj.sp.start_playback(context_uri=f"spotify:playlist:{step['playlist_id']}")
    elif kind == "say":
        from voice.tts_output import speak

        speak(step.get("text", ""))


def _run_routine(rid):
    """Voer een routine uit (ingebouwd of custom). Gooit bij een onbekende id."""
    if rid == "morning":
        from scheduler.routines import morning_routine

        morning_routine()
    elif rid == "bedtime":
        from scheduler.routines import bedtime_routine

        bedtime_routine()
    elif rid in ("party", "desk"):
        lamp = S.lamp(S.lamp_ip(0))
        asyncio.run(lamp.party() if rid == "party" else lamp.bureau())
    else:
        custom = {r["id"]: r for r in (config.get("routines", []) or [])}
        if rid not in custom:
            raise ValueError(f"onbekende routine: {rid}")
        for step in custom[rid].get("steps", []):
            _run_step(step)


@routines_bp.route("/api/routines/run", methods=["POST"])
@require_password
def routines_run():
    rid = (request.get_json(silent=True) or {}).get("id")
    try:
        _run_routine(rid)
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


# ---- Wekker (handmatig vanaf het dashboard) ----
_ALL_IDS = {r["id"] for r in _BUILTIN}


def _alarm_fire():
    rid = config.get("alarm.routine", "morning")
    log("Alarm", f"Dashboard-wekker gaat af -> routine '{rid}'")
    try:
        _run_routine(rid)
    except Exception as exc:  # noqa: BLE001
        log("Alarm", f"Wekker-routine mislukte: {exc}")


_alarm = AlarmScheduler(callback=_alarm_fire)

# Her-arm een bewaarde wekker na een herstart van de service.
_saved = config.get("alarm.time")
if _saved:
    _alarm.set_alarm(_saved)


@routines_bp.route("/api/alarm", methods=["GET"])
def alarm_get():
    dt = _alarm.alarm_time
    return jsonify({
        "set": dt is not None,
        "time": dt.strftime("%H:%M") if dt else config.get("alarm.time"),
        "when": dt.strftime("%Y-%m-%d %H:%M") if dt else None,
        "routine": config.get("alarm.routine", "morning"),
    })


@routines_bp.route("/api/alarm", methods=["POST"])
@require_password
def alarm_set():
    data = request.get_json(silent=True) or {}
    time_str = str(data.get("time", "")).strip()
    routine = str(data.get("routine", "morning")).strip() or "morning"
    if routine not in _ALL_IDS and routine not in {r.get("id") for r in (config.get("routines", []) or [])}:
        return jsonify({"success": False, "error": "onbekende routine"}), 400
    dt = _alarm.set_alarm(time_str)
    if dt is None:
        return jsonify({"success": False, "error": "tijd niet te lezen (gebruik bv. 07:30)"}), 400
    config.set("alarm.time", dt.strftime("%H:%M"))
    config.set("alarm.routine", routine)
    return jsonify({"success": True, "when": dt.strftime("%Y-%m-%d %H:%M")})


@routines_bp.route("/api/alarm", methods=["DELETE"])
@require_password
def alarm_clear():
    _alarm.cancel_alarm()
    config.set("alarm.time", "")
    return jsonify({"success": True})


# ---- Notes ----
@routines_bp.route("/api/notes", methods=["GET"])
def notes_get():
    from logic.notes import list_all_notes

    return jsonify({"notes": list_all_notes()})


@routines_bp.route("/api/notes", methods=["POST"])
def notes_add():
    from logic.notes import add_note

    text = (request.get_json(silent=True) or {}).get("note", "").strip()
    if not text:
        return jsonify({"success": False, "error": "lege notitie"}), 400
    add_note(text)
    return jsonify({"success": True})


@routines_bp.route("/api/notes/<int:index>", methods=["DELETE"])
def notes_delete(index: int):
    from logic.notes import delete_note

    return jsonify({"success": delete_note(index)})
