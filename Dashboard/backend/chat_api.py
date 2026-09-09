"""AI chat: a blocking endpoint and an SSE-streaming one."""
import json
import re

from flask import Blueprint, Response, jsonify, request, stream_with_context

from Dashboard.backend.auth import COOKIE, require_password

chat_bp = Blueprint("chat", __name__)


def _sid(raw) -> str:
    """Sessiesleutel voor de gespreksgeschiedenis: login-cookie (indien gezet)
    + een per-tab id van de client, zodat twee tabs elkaar niet in de rede
    vallen en de historie niet globaal gedeeld is."""
    base = (request.cookies.get(COOKIE, "") or "anon")[:16]
    tab = re.sub(r"[^A-Za-z0-9_-]", "", str(raw or ""))[:64] or "web"
    return f"{base}:{tab}"


@chat_bp.route("/api/send_message", methods=["POST"])
@require_password
def send_message():
    body = request.get_json(silent=True) or {}
    text = (body.get("message") or "").strip()
    if not text:
        return jsonify({"reply": "Typ iets alsjeblieft."})
    from logic.gpt_handler import verwerk_input

    return jsonify({"reply": verwerk_input(text, session=_sid(body.get("sid")))})


@chat_bp.route("/api/chat_stream")
@require_password
def chat_stream():
    text = (request.args.get("message") or "").strip()
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
            yield f"data: {json.dumps({'t': f'[fout: {exc}]'})}\n\n"
        yield "data: {\"done\": true}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
