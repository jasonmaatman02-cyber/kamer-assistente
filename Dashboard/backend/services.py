"""Shared, route-free layer for the dashboard blueprints.

Every external service (Spotify, Tapo lamps, radio/VLC, iCloud calendar) is
created lazily in :func:`svc` and wrapped so a failure degrades one card instead
of crashing the whole dashboard.
"""
from __future__ import annotations

import asyncio
import time

import config

# --------------------------------------------------------------------------- #
# Lazy service registry
# --------------------------------------------------------------------------- #
_services: dict = {}
_errors: dict = {}
_lamp_conns: dict = {}
_health_cache: dict = {}          # name -> (ok, error, expires_at)
_HEALTH_TTL = 60


def svc(name: str):
    """Return a shared service instance, or ``None`` if it can't be created."""
    if name in _services:
        return _services[name]
    try:
        if name == "spotify":
            from sound_system.muziek import SpotifyDJ

            _services[name] = SpotifyDJ()
        elif name == "radio":
            from sound_system.radio import RadioPlayer

            _services[name] = RadioPlayer()
        elif name == "weer":
            from weer.weer import WeerAPI

            _services[name] = WeerAPI()
        elif name == "agenda":
            from scheduler.agenda import AppleCalendarMultiAccount

            _services[name] = AppleCalendarMultiAccount()
        else:
            return None
        _errors.pop(name, None)
    except Exception as exc:  # noqa: BLE001
        _errors[name] = str(exc)
        _services[name] = None
        print(f"[dashboard] service '{name}' niet beschikbaar: {exc}")
    return _services[name]


def errors() -> dict:
    return _errors


def reset_services():
    """Called after a settings/secret change so new config takes effect."""
    _services.clear()
    _errors.clear()
    _health_cache.clear()
    _lamp_conns.clear()
    try:
        from Dashboard.backend.camera_api import camera

        camera.release()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Lamps
# --------------------------------------------------------------------------- #
def lamp(ip: str):
    """Return a connected SlimmeLamp for ``ip``, reusing the connection."""
    from devices.Lights import SlimmeLamp

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
    except Exception:  # noqa: BLE001
        pass
    try:
        for d in sp.devices().get("devices", []):
            if d.get("id"):
                return d["id"]
    except Exception:  # noqa: BLE001
        pass
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
    except Exception:  # noqa: BLE001
        pass


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


def service_status() -> dict:
    now = time.time()
    out = {}
    for n in ("spotify", "radio", "weer", "agenda"):
        cached = _health_cache.get(n)
        if not cached or cached[2] < now:
            ok, err = _probe(n)
            _health_cache[n] = (ok, err, now + _HEALTH_TTL)
        ok, err, _ = _health_cache[n]
        out[n] = {"ok": ok, "error": err}
        if n == "spotify":
            out[n]["needs_relink"] = bool(err and "opnieuw koppelen" in err)
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
        except Exception:  # noqa: BLE001
            pass
    r = svc("radio")
    if r:
        try:
            rd = r.current_station()
            if rd.get("type") == "radio":
                rd["volume"] = r.player.audio_get_volume()
                rd["elapsed"] = int(time.time() - r.start_time) if getattr(r, "start_time", None) else 0
                return rd
        except Exception:  # noqa: BLE001
            pass
    return {"type": "none"}
