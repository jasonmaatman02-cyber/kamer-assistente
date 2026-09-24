"""services._cached() = single-flight + stale-while-revalidate.

Achtergrond (lokaal gereproduceerd met tools/stress_dashboard.py): met een trage
Spotify/weer/agenda stapelden de polls van een paar tabbladen zich op achter de
cache-lock tot alle 16 waitress-workers bezet waren en het HELE dashboard
bevroor (canary-timeouts 7/12, 5/20, 6/16). Nu ververst precies één thread;
de rest krijgt meteen de laatste bekende waarde."""
import threading
import time

import pytest

from Dashboard.backend import services as S


def _run(fn, n, *args):
    out = [None] * n
    ts = []

    def w(i):
        t0 = time.monotonic()
        out[i] = (fn(*args), time.monotonic() - t0)

    for i in range(n):
        t = threading.Thread(target=w, args=(i,))
        ts.append(t)
        t.start()
    return out, ts


def test_followers_get_the_stale_value_instantly_while_one_thread_refreshes():
    S._data_cache["k"] = ({"v": "oud"}, 0.0)             # verlopen maar aanwezig
    release = threading.Event()
    calls = []

    def slow_produce():
        calls.append(1)
        release.wait(5)
        return {"v": "nieuw"}

    refresher = threading.Thread(target=lambda: S._cached("k", 60, slow_produce))
    refresher.start()
    time.sleep(0.1)                                       # refresher houdt nu de lock

    t0 = time.monotonic()
    results = [S._cached("k", 60, slow_produce) for _ in range(10)]
    assert time.monotonic() - t0 < 0.5, "followers wachtten op de trage refresh"
    assert all(r == {"v": "oud"} for r in results)
    assert len(calls) == 1

    release.set()
    refresher.join(3)
    assert S._cached("k", 60, slow_produce) == {"v": "nieuw"}


def test_first_ever_fetch_is_bounded_and_falls_back(monkeypatch):
    monkeypatch.setattr(S, "_CACHE_FIRST_WAIT_S", 0.3)
    release = threading.Event()

    def hanging():
        release.wait(5)
        return {"v": "laat"}

    refresher = threading.Thread(target=lambda: S._cached("nieuw", 60, hanging))
    refresher.start()
    time.sleep(0.1)

    t0 = time.monotonic()
    got = S._cached("nieuw", 60, hanging, fallback={"error": "wordt opgehaald"})
    assert got == {"error": "wordt opgehaald"}
    assert time.monotonic() - t0 < 1.5
    release.set()
    refresher.join(3)


def test_failed_refresh_keeps_the_stale_value_and_retries_next_time():
    S._data_cache["k"] = ({"v": "oud"}, 0.0)

    def boom():
        raise OSError("upstream weg")

    with pytest.raises(OSError):
        S._cached("k", 60, boom)
    assert S._data_cache["k"][0] == {"v": "oud"}          # niet weggegooid
    assert S._cached("k", 60, lambda: {"v": "herstel"}) == {"v": "herstel"}


def test_expire_keeps_stale_value_for_concurrent_readers():
    S._cached("k", 600, lambda: {"v": 1})
    S.invalidate("k")                                     # bv. na een pauze-actie
    release = threading.Event()

    def slow():
        release.wait(5)
        return {"v": 2}

    t = threading.Thread(target=lambda: S._cached("k", 600, slow))
    t.start()
    time.sleep(0.1)
    assert S._cached("k", 600, slow) == {"v": 1}          # oude waarde, niet geblokkeerd
    release.set()
    t.join(3)
    assert S._cached("k", 600, slow) == {"v": 2}


def test_current_playing_is_cached_and_invalidated_by_actions(monkeypatch):
    calls = {"n": 0}

    class DJ:
        def current_track(self):
            calls["n"] += 1
            return {"type": "spotify", "name": f"nummer {calls['n']}"}

    monkeypatch.setitem(S._services, "spotify", DJ())
    a = S.current_playing()
    b = S.current_playing()
    assert a == b and calls["n"] == 1                     # N tabs = 1 Spotify-call
    S.invalidate("now_playing")
    c = S.current_playing()
    assert c["name"] == "nummer 2" and calls["n"] == 2


def test_pause_route_invalidates_now_playing_and_reports_real_failures(client, monkeypatch):
    class DJ:
        last_error = None

        def __init__(self, ok):
            self.ok = ok

        def pauze(self):
            if not self.ok:
                self.last_error = "Geen actief Spotify-apparaat."
            return self.ok

        def current_track(self):
            return {"type": "spotify", "name": "x"}

    dj = DJ(True)
    monkeypatch.setitem(S._services, "spotify", dj)
    S.current_playing()
    assert S._data_cache["now_playing"][1] > 0
    assert client.post("/api/spotify_pause").get_json()["success"] is True
    assert S._data_cache["now_playing"][1] == 0.0         # verlopen -> volgende poll ververst

    monkeypatch.setitem(S._services, "spotify", DJ(False))
    r = client.post("/api/spotify_pause")
    body = r.get_json()
    assert r.status_code == 409 and body["success"] is False and body["no_device"] is True, \
        "pauze zonder actief apparaat werd vroeger als succes gemeld"


def test_service_status_is_single_flight_and_never_blocks_followers(monkeypatch):
    now = time.time()
    for n in ("spotify", "radio", "weer", "agenda"):
        S._health_cache[n] = (True, None, now - 1)        # verlopen: probes nodig
    S._data_cache["spotify:has_device"] = (True, now + 999)
    release = threading.Event()
    probes = []

    def slow_probe(name):
        probes.append(name)
        release.wait(5)
        return True, None

    monkeypatch.setattr(S, "_probe", slow_probe)

    refresher = threading.Thread(target=S.service_status)
    refresher.start()
    time.sleep(0.2)
    n_probes = len(probes)

    t0 = time.monotonic()
    outs = [S.service_status() for _ in range(15)]
    assert time.monotonic() - t0 < 1.0, "service_status wachtte op hangende probes"
    assert all(o["spotify"]["ok"] is True for o in outs)  # oud resultaat geserveerd
    assert len(probes) == n_probes, "volgers startten eigen probes"
    release.set()
    refresher.join(3)


def test_service_status_cold_start_placeholder_is_bounded(monkeypatch):
    monkeypatch.setattr(S, "_CACHE_FIRST_WAIT_S", 0.3)
    release = threading.Event()
    monkeypatch.setattr(S, "_probe", lambda n: (release.wait(5), (True, None))[1])

    refresher = threading.Thread(target=S.service_status)
    refresher.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    out = S.service_status()
    assert time.monotonic() - t0 < 1.5
    assert out["weer"] == {"ok": False, "error": "wordt gecontroleerd"}
    release.set()
    refresher.join(3)
