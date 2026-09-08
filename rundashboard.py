"""Start only the web dashboard."""
from Dashboard.backend.main import app
from logic.logger import log

if __name__ == "__main__":
    log("dashboard", "dashboard gestart")
    app.run(host="0.0.0.0", port=5000, threaded=True)
