"""Start only the web dashboard.

Uses waitress (production WSGI server) when it's installed, else falls back to
the Flask dev server.
"""
from Dashboard.backend.main import app
from logic.logger import log

HOST, PORT = "0.0.0.0", 5000


def main():
    log("dashboard", "dashboard gestart")
    try:
        from waitress import serve

        print(f" * Dashboard (waitress) op http://{HOST}:{PORT}")
        serve(app, host=HOST, port=PORT, threads=8, ident="kamer-dashboard")
    except ImportError:
        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
