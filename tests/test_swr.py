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


def test_spotify_without_credentials_gives_a_friendly_dutch_message():
    """De Media-UI toonde spotipy's kale 'No client_id. Pass it or set a
    SPOTIPY_CLIENT_ID environment variable.'"""
    assert S.svc("spotify") is None
    msg = S.errors()["spotify"]
    assert "Settings" in msg and "client_id" not in msg.lower().replace("client id", "")


# --------------------------------------------------------------------------- #
# Begrensde cache: client-gestuurde keys (?city=, ?start=&end=) mogen geheugen niet laten groeien
# --------------------------------------------------------------------------- #
def test_data_cache_is_bounded_and_keeps_recent_entries():
    from Dashboard.backend import services as S

    for i in range(S._DATA_CACHE_MAX * 4):
        S._cached(f"weather:stad{i}", 600, lambda i=i: {"city": i})
    assert len(S._data_cache) <= S._DATA_CACHE_MAX
    assert len(S._data_cache_locks) <= S._DATA_CACHE_MAX + 8       # de locks van weggegooide keys worden ook opgeruimd
    last = f"weather:stad{S._DATA_CACHE_MAX * 4 - 1}"
    assert last in S._data_cache                                   # het nieuwste blijft


def test_pruning_drops_expired_first_and_never_a_lock_in_use():
    import threading

    from Dashboard.backend import services as S

    S._data_cache.clear()
    S._data_cache_locks.clear()
    held = S._named_lock(S._data_cache_locks, "held")
    held.acquire()
    try:
        for i in range(S._DATA_CACHE_MAX + 50):
            S._data_cache[f"old{i}"] = ("x", 1.0)                  # allemaal al verlopen
        S._store("fresh", 600, {"ok": 1})
        assert "fresh" in S._data_cache and len(S._data_cache) <= S._DATA_CACHE_MAX
        assert "held" in S._data_cache_locks                       # in gebruik: blijft bestaan
    finally:
        held.release()
    assert isinstance(threading.Lock(), type(held))


def test_weather_route_rejects_absurd_city_names(client):
    assert client.get("/api/weather?city=" + "x" * 200).status_code == 400
    assert client.get("/api/weather?city=").status_code == 400
    assert client.get("/api/weather?city=%00%01").status_code == 400
    assert client.get("/api/weather?city=Arnhem").status_code in (200, 503)


def test_weerapi_city_caches_are_bounded(monkeypatch):
    import weer.weer as W

    api = W.WeerAPI()
    monkeypatch.setattr(api, "_fetch_open_meteo", lambda city: {"temp": 1, "city": city})
    for i in range(W._MAX_CACHED_CITIES * 3):
        api.fetch_weather(f"stad{i}")
    assert len(api._cache) <= W._MAX_CACHED_CITIES
    assert f"open-meteo:stad{W._MAX_CACHED_CITIES * 3 - 1}" in api._cache


# --------------------------------------------------------------------------- #
# Een mislukte service-bouw wordt na een minuut opnieuw geprobeerd
# --------------------------------------------------------------------------- #
def test_failed_service_build_is_retried_after_a_minute(monkeypatch):
    from Dashboard.backend import services as S

    S._services.clear()
    S._errors.clear()
    S._build_failed_at.clear()
    clock = {"t": 1000.0}
    monkeypatch.setattr(S, "_mono", lambda: clock["t"])
    attempts = []

    def flaky(name):
        attempts.append(name)
        if len(attempts) < 3:
            raise RuntimeError("audio nog niet klaar")
        return object()

    monkeypatch.setattr(S, "_build", flaky)
    assert S.svc("radio") is None and len(attempts) == 1
    assert S.svc("radio") is None and len(attempts) == 1              # binnen de minuut: geen nieuwe poging
    clock["t"] += 59
    assert S.svc("radio") is None and len(attempts) == 1
    clock["t"] += 2                                                    # > 60 s
    assert S.svc("radio") is None and len(attempts) == 2               # 2e poging faalt weer
    assert "radio" in S._errors
    clock["t"] += 61
    obj = S.svc("radio")
    assert obj is not None and len(attempts) == 3                      # 3e lukt: hersteld zonder herstart
    assert "radio" not in S._errors and "radio" not in S._build_failed_at
    assert S.svc("radio") is obj and len(attempts) == 3                # gelukt: geen verdere pogingen


def test_a_working_service_is_never_rebuilt(monkeypatch):
    from Dashboard.backend import services as S

    S._services.clear()
    S._build_failed_at.clear()
    built = []
    monkeypatch.setattr(S, "_build", lambda name: built.append(name) or object())
    a = S.svc("weer")
    for _ in range(50):
        assert S.svc("weer") is a
    assert built == ["weer"]


def test_reset_services_clears_the_failure_memory():
    from Dashboard.backend import services as S

    S._build_failed_at["x"] = 1.0
    S.reset_services()
    assert S._build_failed_at == {}


def test_repeated_identical_build_failures_are_logged_once(monkeypatch, capsys):
    from Dashboard.backend import services as S

    S._services.clear()
    S._errors.clear()
    S._build_failed_at.clear()
    clock = {"t": 0.0}
    monkeypatch.setattr(S, "_mono", lambda: clock["t"])

    def broken(name):
        raise RuntimeError("Spotify is nog niet ingesteld")

    monkeypatch.setattr(S, "_build", broken)
    for _ in range(5):
        S.svc("spotify")
        clock["t"] += 61
    assert capsys.readouterr().out.count("niet beschikbaar") == 1


# --------------------------------------------------------------------------- #
# 'fresh' (verversen) raakt een externe API hooguit 1x per 15 s
# --------------------------------------------------------------------------- #
def test_fresh_refresh_is_rate_limited_per_key(monkeypatch):
    from Dashboard.backend import services as S

    S._data_cache.clear()
    S._stored_at.clear()
    now = {"t": 5000.0}
    monkeypatch.setattr(S.time, "time", lambda: now["t"])
    fetches = []

    class Weer:
        def fetch_weather(self, city=None):
            fetches.append(city)
            return {"city": "x", "temp": len(fetches)}

    S._services["weer"] = Weer()
    assert S.weather_data(fresh=True)["temp"] == 1
    for _ in range(20):                                     # een script dat de route rammelt
        S.weather_data(fresh=True)
    assert len(fetches) == 1
    now["t"] += 16
    assert S.weather_data(fresh=True)["temp"] == 2          # na 15 s mag verversen weer
    assert len(fetches) == 2


def test_invalidate_after_a_media_action_is_never_rate_limited(monkeypatch):
    from Dashboard.backend import services as S

    S._data_cache.clear()
    S._stored_at.clear()
    n = {"c": 0}

    def produce():
        n["c"] += 1
        return {"v": n["c"]}

    S._cached("now_playing", 60, produce)
    S.invalidate("now_playing")                              # bv. na 'pauze': meteen verse staat tonen
    assert S._cached("now_playing", 60, produce)["v"] == 2


def test_calendar_today_route_does_not_hit_google_on_every_call(client, monkeypatch):
    from Dashboard.backend import services as S

    S._data_cache.clear()
    S._stored_at.clear()
    calls = []

    class Cal:
        error = None
        calendars = [object()]
        fetch_errors = []

        def return_todays_events(self):
            calls.append(1)
            return ["Tandarts om 09:00"]

    S._services["agenda"] = Cal()
    for _ in range(10):
        assert client.get("/api/calendar_today").get_json()["events"] == ["Tandarts om 09:00"]
    assert len(calls) == 1
