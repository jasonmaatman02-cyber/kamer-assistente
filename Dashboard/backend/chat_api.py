"""AI chat: a blocking endpoint and an SSE-streaming one."""
import json
import re

from flask import Blueprint, Response, jsonify, request, stream_with_context

from Dashboard.backend.auth import COOKIE, require_password
from Dashboard.backend.util import json_body

chat_bp = Blueprint("chat", __name__)


def _sid(raw) -> str:
    """Sessiesleutel voor de gespreksgeschiedenis: login-cookie (indien gezet)
    + een per-tab id van de client, zodat twee tabs elkaar niet in de rede
    vallen en de historie niet globaal gedeeld is."""
    base = (request.cookies.get(COOKIE, "") or "anon")[:16]
    tab = re.sub(r"[^A-Za-z0-9_-]", "", str(raw or ""))[:64] or "web"
    return f"{base}:{tab}"


MAX_MESSAGE_CHARS = 8000     # een prompt van MB's laat het lokale model op een Pi minutenlang rekenen


def _message(raw):
    """(tekst, foutmelding): een bericht is een niet-lege tekst van hooguit MAX_MESSAGE_CHARS."""
    if raw is not None and not isinstance(raw, str):
        return "", "bericht moet tekst zijn"
    text = (raw or "").strip()
    if len(text) > MAX_MESSAGE_CHARS:
        return "", f"bericht te lang (max {MAX_MESSAGE_CHARS} tekens)"
    return text, None


@chat_bp.route("/api/chat/reset", methods=["POST"])
@require_password
def chat_reset():
    from logic.gpt_handler import reset_session

    reset_session(_sid(json_body().get("sid")))
    return jsonify({"ok": True})


@chat_bp.route("/api/send_message", methods=["POST"])
@require_password
def send_message():
    body = json_body()
    text, error = _message(body.get("message"))
    if error:
        return jsonify({"reply": error, "error": error}), 400
    if not text:
        return jsonify({"reply": "Typ iets alsjeblieft."})
    from logic.gpt_handler import verwerk_input

    return jsonify({"reply": verwerk_input(text, session=_sid(body.get("sid")))})


@chat_bp.route("/api/chat_stream")
@require_password
def chat_stream():
    text, error = _message(request.args.get("message"))
    if error:
        return jsonify({"error": error}), 400
    if not text:
        return jsonify({"error": "leeg bericht"}), 400
    sid = _sid(request.args.get("sid"))
    from logic.gpt_handler import verwerk_input_stream

    @stream_with_context
    def gen():
        try:
            for piece in verwerk_input_stream(text, session=sid):
                yield f"data: {json.dumps({'t': piece})}\n\n"
        except Exception as exc:  # noqa: BLE001
            from logic.gpt_handler import friendly_ai_error

            yield f"data: {json.dumps({'t': f'[fout: {friendly_ai_error(exc)}]'})}\n\n"
        yield "data: {\"done\": true}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
