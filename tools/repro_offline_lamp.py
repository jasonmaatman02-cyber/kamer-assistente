"""Stresstest: offline Tapo-lampen + open Devices-tabbladen (lokaal, geen hardware).

Draait de ECHTE Flask-app onder waitress (threads=16, zoals productie) op een
tijdelijke config, laat N 'browsertabbladen' open-loop lamp-status pollen (zoals
devices.js) tegen TEST-NET-adressen (blackhole -> echte netwerk-timeouts) en meet
of /api/health responsief blijft.

  python tools/repro_offline_lamp.py                 # DURATION=60 TABS=2 POLL_EVERY=5
  DURATION=90 TABS=4 python tools/repro_offline_lamp.py

Resultaat voor de fix (services.lamp zonder timeout/negative cache): waitress-
queue > 47, health-timeouts. Na de fix: 0 timeouts, lamp-polls falen meteen met 503.
"""
import os, sys, tempfile, threading, time, statistics
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import config
import config.settings as cs

tmp = Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs.ENV_FILE = tmp / ".env"
config.reload()
config.set("devices.lamps", [{"name": "A", "ip": "192.0.2.1"}, {"name": "B", "ip": "192.0.2.2"}])
if os.environ.get("LAMP_TIMEOUT"):
    config.set("devices.lamp_timeout_s", float(os.environ["LAMP_TIMEOUT"]))

from waitress import create_server
from Dashboard.backend.main import app
import requests

server = create_server(app, host="127.0.0.1", port=5099, threads=16)
threading.Thread(target=server.run, daemon=True).start()
time.sleep(0.5)
BASE = "http://127.0.0.1:5099"

DURATION = float(os.environ.get("DURATION", 60))
TABS = int(os.environ.get("TABS", 3))
POLL_EVERY = float(os.environ.get("POLL_EVERY", 2.0))     # echte devices.js: 10s; versneld
stop = threading.Event()
lat = []
lamp_statuses = {}


def one_poll(idx):
    try:
        r = requests.get(f"{BASE}/api/lamp/state?lamp={idx}", timeout=180)
        lamp_statuses[r.status_code] = lamp_statuses.get(r.status_code, 0) + 1
    except Exception as e:
        lamp_statuses[type(e).__name__] = lamp_statuses.get(type(e).__name__, 0) + 1


def tab(i):
    # open-loop, zoals devices.js: setInterval vuurt door, ook als de vorige poll nog loopt
    while not stop.is_set():
        for idx in (0, 1):
            threading.Thread(target=one_poll, args=(idx,), daemon=True).start()
        stop.wait(POLL_EVERY)


for i in range(TABS):
    threading.Thread(target=tab, args=(i,), daemon=True).start()

t_start = time.monotonic()
worst = 0.0
timeouts = 0
while time.monotonic() - t_start < DURATION:
    t0 = time.monotonic()
    try:
        requests.get(f"{BASE}/api/health", timeout=8)
        d = time.monotonic() - t0
    except Exception:
        d = 8.0
        timeouts += 1
    lat.append(d)
    worst = max(worst, d)
    time.sleep(2)
stop.set()
print(f"health-requests: {len(lat)}  timeouts(>8s): {timeouts}  worst: {worst:.1f}s  median: {statistics.median(lat):.2f}s")
print("lamp-state antwoorden:", lamp_statuses)
sys.stdout.flush(); os._exit(0)
