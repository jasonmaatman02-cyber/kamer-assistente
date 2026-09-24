"""systemd-watchdog: laat de service zichzelf herstarten als het dashboard VASTLOOPT.

``Restart=always`` vangt alleen een gecrasht proces op. Een dashboard dat wel draait maar niet
meer antwoordt (alle waitress-workers vast, een deadlock, een C-extensie die de GIL vasthoudt) bleef
zo voor altijd kapot -- precies de klasse fouten die eerder het hele dashboard bevroor.

Werking: ``deploy/kamer-dashboard.service`` zet ``WatchdogSec`` (systemd exporteert dan
``WATCHDOG_USEC`` + ``NOTIFY_SOCKET``). Deze thread vraagt periodiek zijn EIGEN
``/api/config`` op (geen externe afhankelijkheden, dus snel) en stuurt alleen bij een antwoord
``WATCHDOG=1`` naar systemd. Blijft dat uit langer dan ``WatchdogSec``, dan doodt en herstart
systemd de service. Draait het buiten systemd (of zonder WatchdogSec), dan doet dit niets.

Bewust ruim: de probe krijgt 20 s, de eerste 60 s na de start worden altijd doorgemeld (imports op
een Pi), en de unit gebruikt 180 s -- een Pi die even zwaar belast is (Ollama) mag geen valse
herstart uitlokken.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request

_STARTUP_GRACE_S = 60.0
_PROBE_TIMEOUT_S = 20.0


def sd_notify(message: str, environ=None) -> bool:
    """Stuur ``message`` (bv. ``WATCHDOG=1``) naar systemd's NOTIFY_SOCKET. False als dat niet kan."""
    env = os.environ if environ is None else environ
    addr = env.get("NOTIFY_SOCKET")
    if not addr or not hasattr(socket, "AF_UNIX"):
        return False
    if addr.startswith("@"):            # abstract socket
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode("utf-8"))
        return True
    except OSError as exc:
        print(f"[watchdog] kon systemd niet bereiken: {exc}")
        return False


def watchdog_interval(environ=None) -> float | None:
    """Pingfrequentie (een derde van WatchdogSec), of None als er geen watchdog actief is."""
    env = os.environ if environ is None else environ
    raw = env.get("WATCHDOG_USEC")
    if not raw or not env.get("NOTIFY_SOCKET"):
        return None
    try:
        usec = int(raw)
    except ValueError:
        return None
    if usec <= 0:
        return None
    pid = env.get("WATCHDOG_PID")
    if pid and pid.isdigit() and int(pid) != os.getpid():
        return None                      # de watchdog geldt voor een ander proces
    return max(1.0, usec / 1_000_000 / 3)


def http_probe(port: int) -> bool:
    """True als het dashboard binnen de timeout antwoordt (zonder externe afhankelijkheden)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=_PROBE_TIMEOUT_S) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


def run(interval: float, probe, notify, stop: threading.Event | None = None,
        startup_grace: float = _STARTUP_GRACE_S, clock=time.monotonic) -> None:
    """De watchdog-lus (los van systemd/HTTP testbaar): meld elke ``interval`` s "gezond" zolang
    ``probe()`` slaagt (of de opstartperiode nog loopt)."""
    stop = stop or threading.Event()
    started = clock()
    failures = 0
    while not stop.is_set():
        if clock() - started < startup_grace or probe():
            if failures:
                print(f"[watchdog] dashboard antwoordt weer na {failures} mislukte poging(en)")
            failures = 0
            notify("WATCHDOG=1")
        else:
            failures += 1
            print(f"[watchdog] dashboard antwoordt niet ({failures}x) -- systemd herstart de service "
                  "als dit blijft duren")
        stop.wait(interval)


def start(port: int) -> threading.Thread | None:
    """Start de watchdog-thread als systemd een WatchdogSec heeft ingesteld; anders ``None``."""
    interval = watchdog_interval()
    if interval is None:
        return None
    thread = threading.Thread(
        target=run, args=(interval, lambda: http_probe(port), sd_notify), name="watchdog", daemon=True)
    thread.start()
    print(f"[watchdog] actief (elke {interval:.0f}s)")
    return thread
