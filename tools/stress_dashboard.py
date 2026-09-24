"""Stresstest: blijft het dashboard responsief als een externe dienst HANGT?

Draait de echte Flask-app onder waitress (threads=16, zoals productie) op een
tijdelijke config, vervangt de gekozen dienst door een nep die N seconden
blokkeert, laat browser-achtige tabbladen (open-loop, zoals de echte
setInterval-polls) de bijbehorende endpoints pollen en meet /api/config
(zonder externe afhankelijkheden) als 'canary'.

  python tools/stress_dashboard.py spotify
  python tools/stress_dashboard.py weer --hang 25 --tabs 3 --duration 60

Scenario's: spotify | weer | agenda | ollama | radio
Uitkomst: aantal canary-timeouts (>5s) en de mediane/slechtste latency.
"""
import argparse
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

ap = argparse.ArgumentParser()
ap.add_argument("scenario", choices=["spotify", "weer", "agenda", "ollama", "radio"])
ap.add_argument("--hang", type=float, default=20.0, help="seconden dat de nep-dienst blokkeert")
ap.add_argument("--tabs", type=int, default=3)
ap.add_argument("--duration", type=float, default=50.0)
ap.add_argument("--speed", type=float, default=1.0, help="poll-versneller (2 = dubbel zo vaak)")
args = ap.parse_args()

import config
import config.settings as cs

tmp = Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs.ENV_FILE = tmp / ".env"
config.reload()
# config laadt bij import de ECHTE .env in os.environ: scrub de geheimen zodat deze
# lokale run nooit met echte credentials (Tapo/Spotify/Google/mail) iets aanraakt.
import os as _os
for _k in config.SECRET_KEYS:
    _os.environ.pop(_k, None)
config.set("tts.backend", "none")

from Dashboard.backend import services as S


def hang():
    time.sleep(args.hang)


class FakeSp:
    def current_playback(self):
        hang()
        return None

    def devices(self):
        hang()
        return {"devices": []}

    def current_user_playlists(self, limit=40):
        hang()
        return {"items": []}


class FakeDJ:
    sp = FakeSp()

    def current_track(self):
        hang()
        return {"type": "none"}

    def laatste_playlists(self, limit=5):
        hang()
        return []

    def playlists_info(self, **kw):
        hang()
        return []


class FakeWeer:
    def fetch_weather(self, city=None):
        hang()
        return {"city": "x", "temp": 1, "wind": 1, "humidity": 1, "condition": "x"}


class FakeAgenda:
    error = None
    calendars = [object()]

    def return_todays_events(self):
        hang()
        return []

    def get_normalized_events(self, s, e):
        hang()
        return []


class FakeRadio:
    stations = {"a": "x"}

    def play(self, name):
        hang()
        return "ok"

    def current_station(self):
        return {"type": "none", "station": None}


# per scenario: welke dienst hangt, en welke endpoints een browser daarvoor pollt (pad, seconden)
SC = {
    "spotify": ({"spotify": FakeDJ()}, [("/api/overview", 10), ("/api/current_playing", 4),
                                        ("/api/devices", 15), ("/api/service_status", 10)]),
    "weer": ({"weer": FakeWeer()}, [("/api/overview", 10), ("/api/weather", 20)]),
    "agenda": ({"agenda": FakeAgenda()}, [("/api/overview", 10), ("/api/calendar_today", 20),
                                          ("/api/calendar/events?start=2026-09-01&end=2026-09-30", 20)]),
    "radio": ({"radio": FakeRadio()}, [("/api/overview", 10), ("/api/radio_stations", 30)]),
    "ollama": ({}, [("/api/overview", 10)]),
}
fakes, polls = SC[args.scenario]
for name, obj in fakes.items():
    S._services[name] = obj

if args.scenario == "ollama":
    import ai.llm as llm

    def slow_chat(messages, tools=None):
        hang()
        return {"content": "traag antwoord", "tool_calls": []}

    def slow_stream(messages, tools=None):
        hang()
        yield {"type": "done", "content": "traag antwoord"}

    llm.chat, llm.chat_stream = slow_chat, slow_stream
    polls = polls + [("/api/send_message", 0)]

from waitress import create_server
from Dashboard.backend.main import app
import requests

server = create_server(app, host="127.0.0.1", port=5098, threads=16)
threading.Thread(target=server.run, daemon=True).start()
time.sleep(0.5)
BASE = "http://127.0.0.1:5098"
stop = threading.Event()
served = {"n": 0}


def fire(path):
    try:
        if path == "/api/send_message":
            requests.post(BASE + path, json={"message": "hoi", "sid": str(threading.get_ident())}, timeout=200)
        else:
            requests.get(BASE + path, timeout=200)
        served["n"] += 1
    except Exception:
        pass


def tab(i):
    def loop(path, every):
        while not stop.is_set():
            threading.Thread(target=fire, args=(path,), daemon=True).start()
            stop.wait(max(0.5, every / args.speed))
    for path, every in polls:
        if every:
            threading.Thread(target=loop, args=(path, every), daemon=True).start()
    if args.scenario == "ollama":
        # chat: elke tab stuurt (na een timeout) opnieuw een vraag
        while not stop.is_set():
            fire("/api/send_message")


for i in range(args.tabs):
    threading.Thread(target=tab, args=(i,), daemon=True).start()

lat, timeouts = [], 0
t_start = time.monotonic()
while time.monotonic() - t_start < args.duration:
    t0 = time.monotonic()
    try:
        requests.get(BASE + "/api/config", timeout=5)
        d = time.monotonic() - t0
    except Exception:
        d, timeouts = 5.0, timeouts + 1
    lat.append(d)
    time.sleep(1)
stop.set()
print(f"[{args.scenario}] hang={args.hang}s tabs={args.tabs}: canary-requests={len(lat)} "
      f"timeouts(>5s)={timeouts} mediaan={statistics.median(lat):.2f}s slechtst={max(lat):.1f}s "
      f"afgehandelde-polls={served['n']}")
sys.stdout.flush()
os._exit(0)
