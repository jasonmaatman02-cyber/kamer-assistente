"""Routines, notes and the manual dashboard alarm."""
import asyncio
import re
import threading

from flask import Blueprint, jsonify

import config
from Dashboard.backend import services as S
from devices.Lights import describe_lamp_error
from Dashboard.backend.auth import require_password
from logic.logger import log
from scheduler.alarm_manager import alarm as _alarm
from Dashboard.backend.util import json_body

routines_bp = Blueprint("routines", __name__)

# Beschermt de read-modify-write op config.get("routines")/config.set(...) in
# routines_save()/routines_delete() hieronder -- zonder lock kan bij
# (bijna-)gelijktijdige aanroepen (dubbelklik, twee tabbladen) de ene
# wijziging de andere stilzwijgend overschrijven (zelfde patroon als de
# eerder gevonden race in logic/notes.py).
_routines_lock = threading.Lock()
_RID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

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
    data = json_body()
    rid = str(data.get("id", "")).strip()
    name = str(data.get("name", "")).strip()
    if not rid or not name:
        return jsonify({"success": False, "error": "id en naam vereist"}), 400
    if not _RID_RE.fullmatch(rid):
        # de id zit in een URL-pad (DELETE /api/routines/<rid>) en in HTML-attributen
        return jsonify({"success": False, "error": "id: alleen letters, cijfers, - en _ (max 64)"}), 400
    if rid in {r["id"] for r in _BUILTIN}:
        return jsonify({"success": False, "error": "ingebouwde routine-id"}), 400
    steps = data.get("steps") or []
    if not isinstance(steps, list):
        return jsonify({"success": False, "error": "steps moet een lijst zijn"}), 400
    # Grenzen: alles landt in settings.json, dat bij elke wijziging volledig herschreven wordt
    if len(name) > 80 or len(str(data.get("desc", ""))) > 300:
        return jsonify({"success": False, "error": "naam (max 80) of omschrijving (max 300) te lang"}), 400
    if len(steps) > 30 or not all(isinstance(st, dict) for st in steps):
        return jsonify({"success": False, "error": "max 30 stappen, elk een object"}), 400
    with _routines_lock:
        custom = [r for r in (config.get("routines", []) or []) if r.get("id") != rid]
        if len(custom) >= 50:
            return jsonify({"success": False, "error": "max 50 eigen routines"}), 400
        custom.append({"id": rid, "name": name, "desc": data.get("desc", ""), "steps": steps})
        config.set("routines", custom)
    return jsonify({"success": True})


@routines_bp.route("/api/routines/<rid>", methods=["DELETE"])
@require_password
def routines_delete(rid):
    with _routines_lock:
        custom = [r for r in (config.get("routines", []) or []) if r.get("id") != rid]
        config.set("routines", custom)
    return jsonify({"success": True})


def _run_step(step):
    """Eén stap van een custom routine. Gooit door bij een fout — de caller
    (``_run_routine``) vangt 'm op zodat één kapotte stap niet de rest van
    de routine afbreekt."""
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
        if not r:
            raise RuntimeError(S.errors().get("radio") or "radio niet beschikbaar")   # was: stilzwijgend "gelukt"
        r.play(step.get("station") or "radio538")
        if getattr(r, "last_error", None):
            raise RuntimeError(r.last_error)
    elif kind == "spotify":
        if step.get("playlist_id"):
            # sp_dj() gooit een duidelijke fout als Spotify niet is ingesteld (voorheen
            # stilzwijgend niets doen = 'gelukt'), en start_playback() kiest het
            # standaard-apparaat (de Pi) i.p.v. te falen met NO_ACTIVE_DEVICE.
            S.start_playback(S.sp_dj().sp, context_uri=f"spotify:playlist:{step['playlist_id']}")
    elif kind == "say":
        from voice.tts_output import speak

        if speak(step.get("text", "")) is False:
            raise RuntimeError("spraak kon niet worden afgespeeld (TTS/audio)")
    else:
        raise ValueError(f"onbekende actie: {kind}")


class RoutineBusy(RuntimeError):
    """Dezelfde routine draait al."""


# Per routine-id één tegelijk. Een routine is langlopend (LLM-groet, TTS, radio,
# spraakvragen: minuten) en wordt vanaf drie plekken gestart (dashboard-knop,
# wekker, AI-tool). Zonder guard geven een dubbelklik, twee tabbladen of een
# wekker tijdens een handmatige run twee tegelijk lopende routines: dubbele TTS,
# dubbele radio-start en per run een vastgezette waitress-thread.
_running_routines: set = set()
_running_lock = threading.Lock()


def _run_routine(rid):
    """Zie :func:`_run_routine_unguarded`; gooit :class:`RoutineBusy` als deze
    routine al loopt. Geeft een :class:`RoutineResult` terug."""
    with _running_lock:
        if rid in _running_routines:
            raise RoutineBusy(f"routine '{rid}' is al bezig")
        _running_routines.add(rid)
    try:
        return _run_routine_unguarded(rid)
    finally:
        with _running_lock:
            _running_routines.discard(rid)


class RoutineResult:
    """Uitkomst van een routine: mislukte stappen (``failed``) en, als bekend, het
    totaal aantal stappen (``total``). ``everything_failed`` = er is niets gelukt."""

    def __init__(self, failed=None, total=None):
        self.failed = list(failed or [])
        self.total = total

    @property
    def everything_failed(self) -> bool:
        return bool(self.failed) and self.total is not None and len(self.failed) >= self.total


def _run_routine_unguarded(rid):
    """Voer een routine uit (ingebouwd of custom). Gooit alleen bij een
    onbekende id — een fout in één stap van een custom routine wordt hier
    afgevangen zodat de rest van de routine gewoon doorgaat (zie _run_step),
    maar wordt wel in het :class:`RoutineResult` gemeld (geen dummy-succes)."""
    log("ROUTINE", f"Starting routine '{rid}'")
    if rid == "morning":
        from scheduler.routines import morning_routine

        result = RoutineResult(morning_routine())
    elif rid == "bedtime":
        from scheduler.routines import bedtime_routine

        result = RoutineResult(bedtime_routine())
    elif rid in ("party", "desk"):
        result = RoutineResult(total=1)
        try:
            lamp = S.lamp(S.lamp_ip(0))
            asyncio.run(lamp.party() if rid == "party" else lamp.bureau())
        except Exception as exc:  # noqa: BLE001
            log("ROUTINE", f"Light action failed: {exc}")
            result.failed.append(describe_lamp_error(exc))
    else:
        custom = {r["id"]: r for r in (config.get("routines", []) or [])}
        if rid not in custom:
            raise ValueError(f"onbekende routine: {rid}")
        steps = custom[rid].get("steps", [])
        if not isinstance(steps, list):
            steps = []
        result = RoutineResult(total=len(steps))
        for i, step in enumerate(steps, 1):
            try:
                _run_step(step if isinstance(step, dict) else {})
            except Exception as exc:  # noqa: BLE001 - one bad step mag de rest niet stoppen
                kind = step.get("action", "?") if isinstance(step, dict) else "?"
                label = "Light action failed" if kind == "lamp" else f"Step {i} ({kind}) failed"
                log("ROUTINE", f"{label}: {exc}")
                result.failed.append(f"Stap {i} ({kind}): {describe_lamp_error(exc) if kind == 'lamp' else exc}")
            else:
                continue
            log("ROUTINE", "Continuing with next action")
    log("ROUTINE", f"Finished routine '{rid}'")
    return result


@routines_bp.route("/api/routines/run", methods=["POST"])
@require_password
def routines_run():
    rid = json_body().get("id")
    try:
        result = _run_routine(rid)
        if result is not None and result.failed:
            if result.everything_failed:
                return jsonify({"success": False, "error": "; ".join(result.failed)}), 502
            return jsonify({"success": True, "warnings": result.failed})     # deels gelukt
        return jsonify({"success": True})
    except RoutineBusy as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


# ---- Wekker (handmatig vanaf het dashboard) ----
_ALL_IDS = {r["id"] for r in _BUILTIN}


def _alarm_fire():
    rid = config.get("alarm.routine", "morning")
    log("Alarm", f"Dashboard-wekker gaat af -> routine '{rid}'")
    # Eenmalig: na het afgaan meldt de UI 'Geen wekker gezet'. Zonder deze regel
    # her-armde de opstart-code hieronder 'm bij elke herstart (crash, reboot,
    # update) opnieuw, en ging de radio de volgende ochtend ongevraagd weer aan.
    # alarm.time blijft staan als voorinvulling van het tijdveld in de UI.
    config.set("alarm.armed", False)
    try:
        _run_routine(rid)
    except Exception as exc:  # noqa: BLE001
        log("Alarm", f"Wekker-routine mislukte: {exc}")


# Gedeeld AlarmScheduler-object (scheduler/alarm_manager.py) -- zo ziet en
# kan het dashboard ook een wekker annuleren/overschrijven die via de
# spraakflow (scheduler/routines.py::bedtime_routine) is gezet, en
# andersom. Voorheen had elk zijn eigen AlarmScheduler-instantie, waardoor
# een spraak-wekker onzichtbaar en niet te annuleren was vanaf het
# dashboard, en een dashboard-wekker een spraak-wekker niet verving.
_alarm.callback = _alarm_fire

def _should_rearm_saved_alarm() -> bool:
    """Een bewaarde, nog NIET afgegane wekker overleeft een herstart. Oude
    settings.json-bestanden hebben geen 'armed'-sleutel: die tellen als armed
    (gedrag van vóór deze sleutel)."""
    return bool(config.get("alarm.time")) and config.get("alarm.armed", True) is not False


# Her-arm een bewaarde wekker na een herstart van de service.
if _should_rearm_saved_alarm():
    _alarm.set_alarm(config.get("alarm.time"))


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
    data = json_body()
    time_str = str(data.get("time", "")).strip()
    routine = str(data.get("routine", "morning")).strip() or "morning"
    if routine not in _ALL_IDS and routine not in {r.get("id") for r in (config.get("routines", []) or [])}:
        return jsonify({"success": False, "error": "onbekende routine"}), 400
    # scheduler.alarm_manager.set_alarm() (spraak-Q&A) overschrijft deze gedeelde
    # callback met morning_routine; zonder dit zou een dashboard-wekker daarna
    # stilzwijgend die routine draaien i.p.v. de gekozen.
    _alarm.callback = _alarm_fire
    dt = _alarm.set_alarm(time_str)
    if dt is None:
        return jsonify({"success": False, "error": "tijd niet te lezen (gebruik bv. 07:30)"}), 400
    config.set("alarm.time", dt.strftime("%H:%M"))
    config.set("alarm.routine", routine)
    config.set("alarm.armed", True)
    return jsonify({"success": True, "when": dt.strftime("%Y-%m-%d %H:%M")})


@routines_bp.route("/api/alarm", methods=["DELETE"])
@require_password
def alarm_clear():
    _alarm.cancel_alarm()
    config.set("alarm.time", "")
    config.set("alarm.armed", False)
    return jsonify({"success": True})


# ---- Notes ----
@routines_bp.route("/api/notes", methods=["GET"])
def notes_get():
    from logic.notes import list_all_notes

    return jsonify({"notes": list_all_notes()})


@routines_bp.route("/api/notes", methods=["POST"])
def notes_add():
    from logic.notes import add_note

    raw = json_body().get("note", "")
    text = raw.strip() if isinstance(raw, str) else ""      # {"note": 5} gaf een AttributeError
    if not text:
        return jsonify({"success": False, "error": "lege notitie"}), 400
    try:
        add_note(text)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True})


@routines_bp.route("/api/notes/<int:index>", methods=["DELETE"])
def notes_delete(index: int):
    from logic.notes import delete_note

    return jsonify({"success": delete_note(index)})
