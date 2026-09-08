"""Routines, notes and the AI chat endpoint."""
import asyncio

from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S
from Dashboard.backend.auth import require_password

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


@routines_bp.route("/api/routines/run", methods=["POST"])
@require_password
def routines_run():
    rid = (request.get_json(silent=True) or {}).get("id")
    try:
        if rid == "morning":
            from scheduler.routines import morning_routine

            morning_routine()
        elif rid == "bedtime":
            from scheduler.routines import bedtime_routine

            bedtime_routine()
        elif rid in ("party", "desk"):
            ip = S.lamp_ip(0)
            lamp = S.lamp(ip)
            asyncio.run(lamp.party() if rid == "party" else lamp.bureau())
        else:
            custom = {r["id"]: r for r in (config.get("routines", []) or [])}
            if rid not in custom:
                return jsonify({"success": False, "error": "onbekende routine"}), 400
            for step in custom[rid].get("steps", []):
                _run_step(step)
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


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


# ---- Chat ----
@routines_bp.route("/api/send_message", methods=["POST"])
@require_password
def send_message():
    text = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not text:
        return jsonify({"reply": "Typ iets alsjeblieft."})
    from logic.gpt_handler import verwerk_input

    return jsonify({"reply": verwerk_input(text)})
