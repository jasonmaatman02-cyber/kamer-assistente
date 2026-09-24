"""systemd-watchdog: pingt alleen zolang het dashboard antwoordt."""
import os
import socket
import threading

import pytest

from Dashboard.backend import watchdog as W


# --------------------------------------------------------------------------- #
# configuratie uit de omgeving
# --------------------------------------------------------------------------- #
def test_interval_is_a_third_of_watchdog_sec():
    env = {"NOTIFY_SOCKET": "/run/systemd/notify", "WATCHDOG_USEC": "180000000"}
    assert W.watchdog_interval(env) == 60.0


@pytest.mark.parametrize("env", [
    {},                                                              # niet onder systemd
    {"WATCHDOG_USEC": "180000000"},                                  # geen notify-socket
    {"NOTIFY_SOCKET": "/x"},                                         # geen WatchdogSec
    {"NOTIFY_SOCKET": "/x", "WATCHDOG_USEC": "abc"},
    {"NOTIFY_SOCKET": "/x", "WATCHDOG_USEC": "0"},
    {"NOTIFY_SOCKET": "/x", "WATCHDOG_USEC": "1000000", "WATCHDOG_PID": "1"},   # ander proces
])
def test_no_watchdog_when_not_configured(env):
    assert W.watchdog_interval(env) is None


def test_watchdog_pid_of_this_process_is_accepted():
    env = {"NOTIFY_SOCKET": "/x", "WATCHDOG_USEC": "6000000", "WATCHDOG_PID": str(os.getpid())}
    assert W.watchdog_interval(env) == 2.0


def test_start_does_nothing_outside_systemd(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert W.start(5000) is None


# --------------------------------------------------------------------------- #
# de lus
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _drive(probe_results, grace, interval=10.0):
    """Draai de lus voor len(probe_results) rondes met een nep-klok; geef de verstuurde pings terug."""
    clock = _Clock()
    stop = threading.Event()
    sent, probes = [], iter(probe_results)
    rounds = {"n": 0}

    def probe():
        return next(probes)

    def notify(msg):
        sent.append((clock.t, msg))

    real_wait = stop.wait

    def wait(timeout):
        rounds["n"] += 1
        clock.t += timeout
        if rounds["n"] >= len(probe_results) + grace_rounds:
            stop.set()
        return real_wait(0)

    grace_rounds = int(grace // interval)
    stop.wait = wait                       # type: ignore[method-assign]
    W.run(interval, probe, notify, stop=stop, startup_grace=grace, clock=clock)
    return sent


def test_pings_while_the_dashboard_answers():
    sent = _drive([True, True, True], grace=0)
    assert [m for _, m in sent] == ["WATCHDOG=1"] * 3


def test_stops_pinging_when_the_dashboard_stops_answering_after_the_startup_grace():
    sent = _drive([True, False, False, False], grace=0)
    assert len(sent) == 1                  # na de eerste mislukte probe geen pings meer


def test_startup_grace_always_pings_even_if_the_probe_would_fail():
    """Imports op een Pi duren; de eerste minuut mag nooit een herstart uitlokken."""
    sent = _drive([False, False, False], grace=30.0, interval=10.0)
    # rondes 0..2 vallen binnen de grace (t=0,10,20) -> gepingd zonder de probe te raadplegen
    assert len(sent) >= 3


def test_recovery_resumes_pings():
    sent = _drive([False, True, True], grace=0)
    assert len(sent) == 2


# --------------------------------------------------------------------------- #
# echte socket (alleen waar unix-datagram-sockets bestaan)
# --------------------------------------------------------------------------- #
def test_sd_notify_delivers_the_message(tmp_path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("geen AF_UNIX")
    path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        server.bind(path)
    except OSError:
        pytest.skip("unix-datagram-sockets niet ondersteund op dit platform")
    try:
        server.settimeout(2)
        assert W.sd_notify("WATCHDOG=1", {"NOTIFY_SOCKET": path}) is True
        assert server.recv(64) == b"WATCHDOG=1"
    finally:
        server.close()


def test_sd_notify_without_socket_is_false_and_never_raises(tmp_path):
    assert W.sd_notify("WATCHDOG=1", {}) is False
    assert W.sd_notify("WATCHDOG=1", {"NOTIFY_SOCKET": str(tmp_path / "bestaat-niet")}) is False


def test_http_probe_reflects_a_real_server():
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    def app(environ, start_response):
        ok = environ["PATH_INFO"] == "/api/config"
        start_response("200 OK" if ok else "500 Error", [("Content-Type", "application/json")])
        return [b"{}"]

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        assert W.http_probe(port) is True
    finally:
        server.shutdown()
        server.server_close()
    assert W.http_probe(port) is False        # niets meer dat luistert


def test_rundashboard_starts_the_watchdog_and_the_unit_enables_it(monkeypatch):
    import rundashboard

    started = {}
    import Dashboard.backend.presence as presence_mod
    import waitress

    class FakeWorker:
        def start(self):
            pass

    monkeypatch.setattr(presence_mod, "worker", FakeWorker())
    monkeypatch.setattr(waitress, "serve", lambda app, **kw: None)
    monkeypatch.setattr(W, "start", lambda port: started.setdefault("port", port))
    rundashboard.main()
    assert started["port"] == rundashboard.PORT

    unit = (os.path.join(os.path.dirname(__file__), "..", "deploy", "kamer-dashboard.service"))
    text = open(unit, encoding="utf-8").read()
    assert "WatchdogSec=" in text and "NotifyAccess=main" in text


def test_probe_endpoint_stays_open_when_a_dashboard_password_is_set(monkeypatch):
    """De watchdog vraagt /api/config ZONDER login op. Zou dat ooit achter het wachtwoord komen, dan telt
    elke probe als mislukt en herstart systemd de service om de 3 minuten -- een herstartlus die je alleen
    ziet als je een wachtwoord instelt. Deze test bewaakt dat pad."""
    from Dashboard.backend.main import app

    monkeypatch.setenv("DASHBOARD_PASSWORD", "geheim")
    with app.test_client() as c:
        r = c.get("/api/config")
        assert r.status_code == 200 and "camera" in r.get_json()
        assert c.get("/video_feed").status_code == 401        # het wachtwoord is wel actief
