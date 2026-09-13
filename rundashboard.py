"""Start only the web dashboard.

Uses waitress (production WSGI server) when it's installed, else falls back to
the Flask dev server.
"""
from Dashboard.backend.main import app
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
        serve(app, host=HOST, port=PORT, threads=16, ident="kamer-dashboard")
    except ImportError:
        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
