"""Lokale dev-server met een ISOLEERDE, tijdelijke config (raakt nooit de echte
settings.json/.env), camera/presence/TTS uit. Bedoeld voor UI-checks zonder Pi.

  python tools/dev_server.py [poort]        # default 5097

Lampen wijzen naar TEST-NET-adressen (offline), zodat de foutstaten van de UI
getest kunnen worden zonder hardware.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import config
import config.settings as cs

tmp = Path(tempfile.mkdtemp(prefix="kamer-dev-"))
cs.SETTINGS_FILE = tmp / "settings.json"
cs.ENV_FILE = tmp / ".env"
config.reload()
# config laadt bij import de ECHTE .env in os.environ: scrub de geheimen zodat deze
# lokale run nooit met echte credentials (Tapo/Spotify/Google/mail) iets aanraakt.
import os as _os
for _k in config.SECRET_KEYS:
    _os.environ.pop(_k, None)
import logic.logger as _logger
import logic.notes as _notes
_logger.BASE_LOG_DIR = tmp / "logs"            # de echte data/logs blijft onaangeraakt
_notes.NOTES_FILE = tmp / "notes.json"         # idem data/notes.json
config.set("camera.enabled", False)
config.set("presence.enabled", False)
config.set("tts.backend", "none")
config.set("devices.lamps", [{"name": "Bureaulamp", "ip": "192.0.2.1"}, {"name": "Slaapkamer", "ip": "192.0.2.2"}])

from waitress import serve

from Dashboard.backend.main import app

port = int(sys.argv[1]) if len(sys.argv) > 1 else 5097
print(f"dev-server op http://127.0.0.1:{port}  (config: {tmp})", flush=True)
serve(app, host="127.0.0.1", port=port, threads=16)
