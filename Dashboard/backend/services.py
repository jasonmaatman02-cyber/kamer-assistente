"""Shared, route-free layer for the dashboard blueprints.

Every external service (Spotify, Tapo lamps, radio/VLC, iCloud calendar) is
created lazily in :func:`svc` and wrapped so a failure degrades one card instead
of crashing the whole dashboard.
"""
from __future__ import annotations

import asyncio
import threading
import time

import config
from logic.logger import log

# --------------------------------------------------------------------------- #
# Lazy service registry
# --------------------------------------------------------------------------- #
_services: dict = {}
_errors: dict = {}
_lamp_conns: dict = {}
_lamp_locks: dict = {}            # ip -> Lock (één verbinding per lamp tegelijk)
_health_cache: dict = {}          # name -> (ok, error, expires_at)
_HEALTH_TTL = 60
_reg_lock = threading.RLock()     # beschermt het aanmaken van services/lampsloten


def _build(name: str):
    if name == "spotify":
        from sound_system.muziek import SpotifyDJ
        return SpotifyDJ()
    if name == "radio":
        from sound_system.radio import RadioPlayer
        return RadioPlayer()
    if name == "weer":
        from weer.weer import WeerAPI
        return WeerAPI()
    if name == "agenda":
        from scheduler.agenda import AppleCalendarMultiAccount
        return AppleCalendarMultiAccount()
    raise KeyError(name)


def svc(name: str):
    """Return a shared service instance, or ``None`` if it can't be created."""
    if name in _services:            # snelle weg, geen lock nodig
        return _services[name]
    with _reg_lock:                  # één thread bouwt, de rest wacht
        if name in _services:
            return _services[name]
        try:
            _services[name] = _build(name)
            _errors.pop(name, None)
        except KeyError:
            return None
        except Exception as exc:  # noqa: BLE001 - één storing mag niet het hele dashboard slopen
            first_time = _errors.get(name) != str(exc)
            _errors[name] = str(exc)
            _services[name] = None
            print(f"[dashboard] service '{name}' niet beschikbaar: {exc}")
            if first_time:  # niet elke poll opnieuw in het logboek spammen
                log("Service", f"{name} niet beschikbaar: {exc}")
        return _services[name]


def errors() -> dict:
    return _errors


def reset_services():
    """Called after a settings/secret change so new config takes effect."""
    with _reg_lock:
        _services.clear()
        _errors.clear()
        _health_cache.clear()
        _lamp_conns.clear()
        _lamp_locks.clear()
    try:
        from Dashboard.backend.camera_api import camera

        camera.release()
    except Exception:  # noqa: BLE001
        pass


def _is_expected(exc: BaseException) -> bool:
    """Netwerk/API-fouten zijn normaal (dienst even weg); alles anders
    (KeyError, AttributeError, TypeError...) wijst op een bug en hoort in
    het journaal."""
    import spotipy

    return isinstance(exc, (spotipy.SpotifyException, OSError))


def _degrade(where: str, exc: BaseException) -> None:
    """Stil bij een verwachte storing; anders een breadcrumb naar stdout
    (journalctl), niet naar het notificatie-logboek — geen spam daar."""
    if not _is_expected(exc):
        print(f"[services] {where}: onverwachte fout {exc!r}")


# --------------------------------------------------------------------------- #
# Lamps
# --------------------------------------------------------------------------- #
def lamp(ip: str):
    """Return a connected SlimmeLamp for ``ip``, reusing the connection.
    Eén verbindingspoging per lamp tegelijk (connect() is een trage netwerkcall)."""
    from devices.Lights import SlimmeLamp

    existing = _lamp_conns.get(ip)
    if existing is not None and existing.lamp is not None:
        return existing
    with _reg_lock:
        lk = _lamp_locks.setdefault(ip, threading.Lock())
    with lk:
        existing = _lamp_conns.get(ip)
        if existing is not None and existing.lamp is not None:
            return existing
        obj = SlimmeLamp(config.secret("TAPO_USER"), config.secret("TAPO_PASSWORD"), ip)
        asyncio.run(obj.connect())
        _lamp_conns[ip] = obj
        return obj


def drop_lamp(ip: str):
    _lamp_conns.pop(ip, None)


def lamp_ip(name_or_index):
    lamps = config.get("devices.lamps", [])
    if isinstance(name_or_index, int) or (isinstance(name_or_index, str) and name_or_index.isdigit()):
        idx = int(name_or_index)
        if 0 <= idx < len(lamps):
            return lamps[idx]["ip"]
    for entry in lamps:
        if str(name_or_index).lower() in entry.get("name", "").lower():
            return entry["ip"]
    return lamps[0]["ip"] if lamps else None


# --------------------------------------------------------------------------- #
# Spotify
# --------------------------------------------------------------------------- #
def sp_dj():
    s = svc("spotify")
    if not s:
        raise RuntimeError(_errors.get("spotify", "Spotify niet beschikbaar"))
    return s


def spotify_oauth():
    from pathlib import Path

    from spotipy.oauth2 import SpotifyOAuth

    from sound_system.muziek import SPOTIFY_SCOPE

    cid = config.secret("SPOTIFY_CLIENT_ID")
    csecret = config.secret("SPOTIFY_CLIENT_SECRET")
    if not (cid and csecret):
        return None
    return SpotifyOAuth(
        client_id=cid,
        client_secret=csecret,
        redirect_uri=config.secret("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback"),
        scope=SPOTIFY_SCOPE,
        open_browser=False,
        show_dialog=True,
        cache_path=str(Path(__file__).resolve().parent.parent.parent / ".cache"),
    )


def active_device_id(sp):
    """Actief apparaat, anders het eerste beschikbare, anders None."""
    try:
        pb = sp.current_playback()
        if pb and pb.get("device", {}).get("id"):
            return pb["device"]["id"]
    except Exception as exc:  # noqa: BLE001
        _degrade("active_device_id/current_playback", exc)
    try:
        for d in sp.devices().get("devices", []):
            if d.get("id"):
                return d["id"]
    except Exception as exc:  # noqa: BLE001
        _degrade("active_device_id/devices", exc)
    return None


def wake_device(sp, device_id):
    """Een idle Spotify-app accepteert 'afspelen' wel maar begint niet. Neem het
    apparaat eerst over zodat het echt actief is."""
    try:
        pb = sp.current_playback()
        active = pb and pb.get("device", {}).get("id") == device_id and pb.get("is_playing")
        if not active:
            sp.transfer_playback(device_id, force_play=True)
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001
        _degrade("wake_device", exc)


# --------------------------------------------------------------------------- #
# Data helpers (used by the individual routes and by /api/overview)
# --------------------------------------------------------------------------- #
def system_stats() -> dict:
    try:
        import psutil

        vm = psutil.virtual_memory()
        du = psutil.disk_usage("/")
        temp = None
        try:
            temps = psutil.sensors_temperatures()
            for key in ("cpu_thermal", "cpu-thermal", "coretemp", "soc_thermal"):
                if key in temps and temps[key]:
                    temp = round(temps[key][0].current, 1)
                    break
        except Exception:  # noqa: BLE001
            pass
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "mem_percent": vm.percent,
            "mem_used_mb": round(vm.used / 1e6),
            "mem_total_mb": round(vm.total / 1e6),
            "disk_percent": du.percent,
            "disk_free_gb": round(du.free / 1e9, 1),
            "cpu_temp": temp,
            "uptime_s": int(time.time() - psutil.boot_time()),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _probe(name: str):
    s = svc(name)
    if s is None:
        return False, _errors.get(name, "niet beschikbaar")
    try:
        if name == "spotify":
            s.sp.current_user()
        elif name == "weer":
            if not s.fetch_weather():
                return False, "geen weerdata"
        elif name == "agenda":
            if getattr(s, "error", None):
                return False, s.error
            if not getattr(s, "calendars", None):
                return False, "geen agenda's gevonden"
        return True, None
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if name == "spotify" and "invalid_grant" in msg:
            return False, "Spotify opnieuw koppelen (token verlopen)"
        return False, msg


def _spotify_has_device() -> bool:
    """Gecached (TTL): is er een Spotify-apparaat om op af te spelen?"""
    now = time.time()
    cached = _health_cache.get("spotify_device")
    if cached and cached[1] >= now:
        return cached[0]
    s = _services.get("spotify")
    try:
        has = bool(s and active_device_id(s.sp))
    except Exception:  # noqa: BLE001 - check zelf mag niet zeuren
        has = True
    _health_cache["spotify_device"] = (has, now + _HEALTH_TTL)
    return has


def service_status() -> dict:
    now = time.time()
    names = ("spotify", "radio", "weer", "agenda")

    # verlopen probes parallel doen — scheelt seconden op trage wifi
    stale = [n for n in names
             if not (_health_cache.get(n) and _health_cache[n][2] >= now)]
    if stale:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(stale)) as ex:
            for n, res in zip(stale, ex.map(_probe, stale)):
                _health_cache[n] = (res[0], res[1], now + _HEALTH_TTL)

    out = {}
    for n in names:
        ok, err, _ = _health_cache[n]
        out[n] = {"ok": ok, "error": err}
        if n == "spotify":
            relink = bool(err and "opnieuw koppelen" in err)
            out[n]["needs_relink"] = relink
            if ok and not relink and not _spotify_has_device():
                out[n]["no_device"] = True
                out[n]["error"] = "geen apparaat actief"
    return out


def weather_data(city=None) -> dict:
    w = svc("weer")
    if not w:
        return {"error": _errors.get("weer", "weer niet beschikbaar")}
    return w.fetch_weather(city) or {"error": "geen weerdata"}


def calendar_today() -> dict:
    cal = svc("agenda")
    if not cal:
        return {"events": [], "error": _errors.get("agenda")}
    try:
        return {"events": cal.return_todays_events()}
    except Exception as exc:  # noqa: BLE001
        return {"events": [], "error": str(exc)}


def current_playing() -> dict:
    sp = svc("spotify")
    if sp:
        try:
            data = sp.current_track()
            if data.get("type") == "spotify":
                pb = sp.sp.current_playback()
                data["volume"] = (pb or {}).get("device", {}).get("volume_percent", 50)
                return data
        except Exception as exc:  # noqa: BLE001
            _degrade("current_playing/spotify", exc)
    r = svc("radio")
    if r:
        try:
            rd = r.current_station()
            if rd.get("type") == "radio":
                rd["volume"] = r.player.audio_get_volume()
                rd["elapsed"] = int(time.time() - r.start_time) if getattr(r, "start_time", None) else 0
                return rd
        except Exception as exc:  # noqa: BLE001
            _degrade("current_playing/radio", exc)
    return {"type": "none"}
