"""AI chat: a blocking endpoint and an SSE-streaming one."""
import json

from flask import Blueprint, Response, jsonify, request, stream_with_context

from Dashboard.backend.auth import require_password

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/api/send_message", methods=["POST"])
@require_password
def send_message():
    text = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not text:
        return jsonify({"reply": "Typ iets alsjeblieft."})
    from logic.gpt_handler import verwerk_input

    return jsonify({"reply": verwerk_input(text)})


@chat_bp.route("/api/chat_stream")
@require_password
def chat_stream():
    text = (request.args.get("message") or "").strip()
    if not text:
        return jsonify({"error": "leeg bericht"}), 400
    from logic.gpt_handler import verwerk_input_stream

    @stream_with_context
    def gen():
        try:
            for piece in verwerk_input_stream(text):
                yield f"data: {json.dumps({'t': piece})}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'t': f'[fout: {exc}]'})}\n\n"
        yield "data: {\"done\": true}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
