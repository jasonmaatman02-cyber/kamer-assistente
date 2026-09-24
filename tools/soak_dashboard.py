"""Soak-test: lekt het dashboard threads / geheugen / handles onder langdurige, gemengde belasting?

Draait de echte Flask-app onder waitress (threads=16, zoals productie) op een
tijdelijke config, met de lastige omstandigheden van de Pi tegelijk:
  * presence-worker AAN en geen (werkende) camera  -> backoff/herstel-pad
  * twee onbereikbare Tapo-lampen (TEST-NET-adressen) -> connect-timeouts
  * geen Spotify/weer/agenda-keys                    -> foutpaden
en pollt/bedient daarna een mix van alle GET-endpoints plus schrijvende
acties (notities, wekker, lamp, routine) via een paar client-threads.

  python tools/soak_dashboard.py --duration 240
  python tools/soak_dashboard.py --duration 900 --rate 12

Rapport: threads / RSS / handles over tijd (eerste vs. laatste stabiele meting),
5xx per endpoint, latency (p50/p95/max) en de /api/config-canary.
Slaagt (exit 0) als threads/handles niet groeien en er geen onverwachte 500's zijn.
"""
import argparse
import http.client
import json
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

ap = argparse.ArgumentParser()
ap.add_argument("--duration", type=float, default=240.0)
ap.add_argument("--rate", type=float, default=8.0, help="requests/s over alle clients")
ap.add_argument("--clients", type=int, default=4)
ap.add_argument("--port", type=int, default=5098)
ap.add_argument("--warmup", type=float, default=40.0, help="seconden voor de basislijn-meting")
args = ap.parse_args()

import config
import config.settings as cs

tmp = Path(tempfile.mkdtemp(prefix="soak_"))
cs.SETTINGS_FILE = tmp / "settings.json"
cs.ENV_FILE = tmp / ".env"
config.reload()
for _k in config.SECRET_KEYS:          # nooit met echte credentials iets aanraken
    os.environ.pop(_k, None)
import logic.logger as _logger
_logger.BASE_LOG_DIR = tmp / "logs"    # niet in de echte data/logs schrijven
import logic.notes as _notes
_notes.NOTES_FILE = tmp / "notes.json"      # de echte data/notes.json mag niet veranderen

config.set("tts.backend", "none")
config.set("presence.enabled", True)
config.set("presence.interval_s", 1.0)
config.set("camera.enabled", True)
config.set("devices.lamps", [{"name": "A", "ip": "192.0.2.11"}, {"name": "B", "ip": "192.0.2.12"}])
config.set("devices.lamp_timeout_s", 2)

import psutil

from Dashboard.backend.main import app

proc = psutil.Process()


def handles():
    try:
        return proc.num_handles()
    except AttributeError:
        return proc.num_fds()


def start_server():
    from waitress import serve

    t = threading.Thread(
        target=lambda: serve(app, host="127.0.0.1", port=args.port, threads=16,
                             channel_timeout=300, ident="soak"),
        daemon=True, name="soak-waitress")
    t.start()
    for _ in range(100):
        try:
            c = http.client.HTTPConnection("127.0.0.1", args.port, timeout=1)
            c.request("GET", "/api/config")
            c.getresponse().read()
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("server startte niet")


from Dashboard.backend.presence import worker as presence_worker

start_server()
presence_worker.start()

GET = [
    "/", "/api/overview", "/api/config", "/api/health", "/api/notifications?limit=50", "/api/devices",
    "/api/presence", "/api/camera_status", "/api/lamps", "/api/lamp/state", "/api/current_playing",
    "/api/service_status", "/api/system_stats", "/api/notes", "/api/routines", "/api/alarm",
    "/api/settings", "/api/radio_stations", "/api/weather", "/api/calendar_today",
    "/api/calendar/events", "/api/camera_snapshot", "/api/playlists", "/api/last_played_playlists",
    "/api/search_spotify?q=abba", "/api/thermostat", "/api/auth/status", "/api/speedtest",
]
WRITE = [
    ("POST", "/api/notes", {"note": "soak"}),
    ("PUT", "/api/lamp/on", {}),
    ("PUT", "/api/lamp/off", {}),
    ("POST", "/api/lamp/mode/desk", {}),
    ("POST", "/api/alarm", {"time": "07:30", "routine": "morning"}),
    ("POST", "/api/routines/run", {"id": "desk"}),
    ("POST", "/api/spotify_pause", {}),
    ("POST", "/api/radio_stop", {}),
]

lat = defaultdict(list)
codes = defaultdict(Counter)
canary = []
stop = threading.Event()
lock = threading.Lock()


def one(method, path, body=None, timeout=60):
    t0 = time.perf_counter()
    try:
        c = http.client.HTTPConnection("127.0.0.1", args.port, timeout=timeout)
        payload = json.dumps(body) if body is not None else None
        c.request(method, path, body=payload, headers={"Content-Type": "application/json"} if payload else {})
        r = c.getresponse()
        r.read()
        code = r.status
        if code >= 500 and "json" not in (r.getheader("Content-Type") or ""):
            code = f"{code}-html"          # ongevangen exceptie (Flask-foutpagina), geen nette JSON-fout
        c.close()
    except Exception as exc:  # noqa: BLE001
        code = type(exc).__name__
    dt = time.perf_counter() - t0
    key = f"{method} {path.split('?')[0]}"
    with lock:
        lat[key].append(dt)
        codes[key][code] += 1
    return code, dt


def client(idx):
    rnd = random.Random(idx)
    gap = args.clients / max(args.rate, 0.1)
    while not stop.is_set():
        if rnd.random() < 0.85:
            one("GET", rnd.choice(GET))
        else:
            m, p, b = rnd.choice(WRITE)
            one(m, p, b)
            if p == "/api/notes":            # notities weer opruimen zodat het bestand niet groeit
                one("DELETE", "/api/notes/0")
        stop.wait(gap * rnd.uniform(0.5, 1.5))


def canary_loop():
    while not stop.is_set():
        code, dt = one("GET", "/api/config", timeout=20)
        canary.append((code, dt))
        stop.wait(1.0)


samples = []


def sample(t):
    rss = proc.memory_info().rss / 1e6
    samples.append({"t": round(t), "threads": threading.active_count(), "rss_mb": round(rss, 1),
                    "handles": handles(), "cpu": proc.cpu_percent(interval=None)})


proc.cpu_percent(interval=None)
threads = [threading.Thread(target=client, args=(i,), daemon=True) for i in range(args.clients)]
threads.append(threading.Thread(target=canary_loop, daemon=True))
for t in threads:
    t.start()

t0 = time.time()
print(f"soak {args.duration:.0f}s, {args.rate}/s, {args.clients} clients -- tmp {tmp}")
print(f"{'t':>5} {'thr':>4} {'rssMB':>7} {'hdl':>5} {'cpu%':>5}")
while time.time() - t0 < args.duration:
    time.sleep(10)
    sample(time.time() - t0)
    s = samples[-1]
    print(f"{s['t']:>5} {s['threads']:>4} {s['rss_mb']:>7} {s['handles']:>5} {s['cpu']:>5}")
    sys.stdout.flush()
stop.set()
time.sleep(1)

# ---------------------------------------------------------------- rapport
base = next((s for s in samples if s["t"] >= args.warmup), samples[0])
last = samples[-1]
print("\n== groei sinds t=%ds ==" % base["t"])
d_thr = last["threads"] - base["threads"]
d_hdl = last["handles"] - base["handles"]
d_rss = last["rss_mb"] - base["rss_mb"]
print(f"threads {base['threads']} -> {last['threads']} ({d_thr:+d}); handles {base['handles']} -> {last['handles']} ({d_hdl:+d}); "
      f"RSS {base['rss_mb']} -> {last['rss_mb']} MB ({d_rss:+.1f})")

print("\n== endpoints (n, p50, p95, max ms, statuscodes) ==")
bad = []
for key in sorted(lat):
    v = sorted(lat[key])
    p50 = statistics.median(v) * 1000
    p95 = v[min(len(v) - 1, int(len(v) * 0.95))] * 1000
    cs_ = dict(codes[key])
    print(f"{key:38} n={len(v):4} p50={p50:7.0f} p95={p95:7.0f} max={max(v)*1000:7.0f}  {cs_}")
    for code, n in cs_.items():
        if not isinstance(code, int):          # exceptie of "5xx-html" = ongevangen fout; JSON-5xx (lamp offline e.d.) is bewust
            bad.append((key, code, n))

slow = [dt for code, dt in canary if dt > 5 or not isinstance(code, int)]
cv = [dt for _, dt in canary]
print(f"\ncanary /api/config: n={len(cv)} median={statistics.median(cv)*1000:.0f}ms max={max(cv)*1000:.0f}ms >5s: {len(slow)}")
print("presence:", json.dumps(presence_worker.status()))
print("ongevangen fouten (5xx-html / client-exceptions):", bad or "geen")

fail = []
if d_thr > 3:
    fail.append(f"thread-groei {d_thr:+d}")
if d_hdl > 20:
    fail.append(f"handle-groei {d_hdl:+d}")
if slow:
    fail.append(f"{len(slow)} canary-timeouts")
if bad:
    fail.append("ongevangen fouten")
print("\nRESULTAAT:", "FAALT: " + "; ".join(fail) if fail else "OK")
sys.stdout.flush()
os._exit(1 if fail else 0)
