"""Start only the web dashboard.

Uses waitress (production WSGI server) when it's installed, else falls back to
the Flask dev server.
"""
from Dashboard.backend.main import MAX_BODY_BYTES, app
from logic.logger import log

HOST, PORT = "0.0.0.0", 5000


def main():
    log("dashboard", "dashboard gestart")

    # Aanwezigheidsdetectie: één achtergrond-thread, doet niks zolang
    # presence.enabled uit staat (Settings). Hoort hier en niet in
    # Dashboard/backend/main.py — dat mag geen hardware aanraken bij import
    # (de testsuite importeert die module continu).
    from Dashboard.backend.presence import worker as presence_worker

    presence_worker.start()

    try:
        from waitress import serve

        print(f" * Dashboard (waitress) op http://{HOST}:{PORT}")
        # ruim aantal threads: een /video_feed-kijker houdt er de hele tijd één
        # bezet (camera.max_viewers begrenst dat), de rest blijft vrij
        #
        # channel_timeout: waitress' eigen default (120s) is te kort voor de
        # AI-chatstream (/api/chat_stream) -- op een Pi 4B kan het eerste
        # bericht van een gesprek (lang, ongecached prompt incl. alle tool-
        # schema's) een paar minuten prompt processing kosten vóórdat het
        # eerste token binnenkomt; in die stille periode stuurt de SSE-stream
        # nog niks, en waitress kapt een kanaal zonder dataverkeer anders af
        # (live gemeten op de Pi: één 1407-token bericht duurde 3m45s totaal).
        serve(app, host=HOST, port=PORT, threads=16, channel_timeout=300, ident="kamer-dashboard",
              max_request_body_size=MAX_BODY_BYTES)
    except ImportError:
        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
