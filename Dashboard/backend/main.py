"""Flask app for the Smart Home dashboard.

Design notes
------------
* **Nothing hardware/network-y happens at import time.** Every external service
  (Spotify, Tapo lamps, radio/VLC, iCloud calendar, camera) is created on first
  use inside :func:`svc` and wrapped so a failure degrades one card instead of
  crashing the whole dashboard. This matters on a Raspberry Pi where wifi may be
  down at boot.
* All tunables (camera resolution/fps/quality, poll intervals, city, feature
  flags) come from :mod:`config` and are editable from the Settings tab.
"""
from __future__ import annotations

import asyncio
import secrets as _secrets
import threading
import time

from flask import Flask, jsonify, render_template, request, Response

import config

app = Flask(__name__, template_folder="../", static_folder="../static")

# --------------------------------------------------------------------------- #
# Lazy service registry
# --------------------------------------------------------------------------- #
_services: dict = {}
_errors: dict = {}


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


def reset_services():
    """Called after settings change so new config takes effect."""
    _services.clear()
    _errors.clear()
    _health_cache.clear()
    _lamp_conns.clear()
    _camera.release()


_lamp_conns: dict = {}


def _lamp(ip: str):
    """Return a connected SlimmeLamp for ``ip``, reusing the connection."""
    from devices.Lights import SlimmeLamp

    lamp = _lamp_conns.get(ip)
    if lamp is not None and lamp.lamp is not None:
        return lamp
    lamp = SlimmeLamp(config.secret("TAPO_USER"), config.secret("TAPO_PASSWORD"), ip)
    asyncio.run(lamp.connect())
    _lamp_conns[ip] = lamp
    return lamp


def _lamp_ip(name_or_index):
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
# Pages
# --------------------------------------------------------------------------- #
_PAGES = {
    "main": "main.html",
    "devices": "devices.html",
    "media": "media.html",
    "environment": "environment.html",
    "routines": "routines.html",
    "notes": "notes.html",
    "chat": "chat.html",
    "notifications": "notifications.html",
    "camera": "camera.html",
    "settings": "settings.html",
}


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/")
@app.route("/<page>")
def page(page: str = "main"):
    template = _PAGES.get(page)
    if not template:
        return render_template("main.html", active="main"), 404
    return render_template(template, active=page)


# --------------------------------------------------------------------------- #
# Settings API
# --------------------------------------------------------------------------- #
@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify({"settings": config.all_settings(), "defaults": config.DEFAULTS, "errors": _errors})


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    patch = request.get_json(silent=True) or {}
    if not isinstance(patch, dict):
        return jsonify({"success": False, "error": "verwacht een JSON-object"}), 400
    config.update(patch)
    reset_services()
    return jsonify({"success": True, "settings": config.all_settings()})


@app.route("/api/config")
def api_config():
    """Small, public subset the frontend needs on every page."""
    return jsonify({
        "poll": config.get("dashboard"),
        "camera": {
            "browser_detection": config.get("camera.browser_detection"),
            "enabled": config.get("camera.enabled"),
        },
        "features": config.get("features"),
    })


# --------------------------------------------------------------------------- #
# Secrets editor — unlocked with a one-time code e-mailed to RECEIVER.
# --------------------------------------------------------------------------- #
_otp = {"code": None, "expires": 0.0}
_unlock_sessions: dict[str, float] = {}
_UNLOCK_TTL = 30 * 60
_OTP_TTL = 10 * 60


def _mail_ready() -> bool:
    return bool(
        config.secret("EMAIL_ADDRESS")
        and config.secret("EMAIL_PASSWORD")
        and config.secret("RECEIVER")
    )


def _unlocked() -> bool:
    token = request.headers.get("X-Unlock-Token", "")
    exp = _unlock_sessions.get(token)
    if exp and time.time() < exp:
        return True
    _unlock_sessions.pop(token, None)
    return False


@app.route("/api/auth/request-code", methods=["POST"])
def api_auth_request_code():
    if not _mail_ready():
        return jsonify({
            "ok": False,
            "error": "Mail nog niet ingesteld. Vul EMAIL_ADDRESS, EMAIL_PASSWORD en "
                     "RECEIVER eenmalig in via het .env-bestand op de Pi.",
        }), 400
    from logic.mail_sender import send_email_message

    code = f"{_secrets.randbelow(1_000_000):06d}"
    _otp.update(code=code, expires=time.time() + _OTP_TTL)
    result = send_email_message(
        "Kamer-assistent: ontgrendelcode",
        f"Je code om de instellingen te ontgrendelen: {code}\n\n"
        f"Verloopt over 10 minuten. Niet aangevraagd? Negeer deze mail.",
    )
    if "mislukt" in result.lower():
        return jsonify({"ok": False, "error": "Kon de mail niet versturen — check de Gmail-gegevens."}), 502
    return jsonify({"ok": True})


@app.route("/api/auth/verify", methods=["POST"])
def api_auth_verify():
    code = (request.get_json(silent=True) or {}).get("code", "").strip()
    if not _otp["code"] or time.time() > _otp["expires"] or code != _otp["code"]:
        return jsonify({"ok": False, "error": "Code ongeldig of verlopen"}), 401
    _otp.update(code=None, expires=0.0)
    token = _secrets.token_urlsafe(24)
    _unlock_sessions[token] = time.time() + _UNLOCK_TTL
    return jsonify({"ok": True, "token": token, "ttl": _UNLOCK_TTL})


@app.route("/api/secrets", methods=["GET"])
def api_secrets_get():
    return jsonify({
        "secrets": config.secret_status(),
        "keys": config.SECRET_KEYS,
        "unlocked": _unlocked(),
        "mail_ready": _mail_ready(),
    })


@app.route("/api/secrets", methods=["POST"])
def api_secrets_set():
    if not _unlocked():
        return jsonify({"ok": False, "error": "Niet ontgrendeld"}), 403
    data = request.get_json(silent=True) or {}
    changed = []
    for key, value in data.items():
        if key in config.SECRET_KEYS and isinstance(value, str):
            try:
                config.set_secret(key, value)
                changed.append(key)
            except Exception as exc:  # noqa: BLE001
                return jsonify({"ok": False, "error": f"{key}: {exc}"}), 500
    config.reload()
    reset_services()
    return jsonify({"ok": True, "changed": changed})


# --------------------------------------------------------------------------- #
# Spotify OAuth helper (headless: get URL -> user authorises -> pastes redirect)
# --------------------------------------------------------------------------- #
def _spotify_oauth():
    from spotipy.oauth2 import SpotifyOAuth
    from pathlib import Path
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
        show_dialog=True,   # altijd de volledige scope-lijst laten bevestigen
        cache_path=str(Path(__file__).resolve().parent.parent.parent / ".cache"),
    )


@app.route("/api/spotify/auth-url")
def api_spotify_auth_url():
    if not _unlocked():
        return jsonify({"ok": False, "error": "Niet ontgrendeld"}), 403
    oauth = _spotify_oauth()
    if not oauth:
        return jsonify({"ok": False, "error": "Vul eerst SPOTIFY_CLIENT_ID en SPOTIFY_CLIENT_SECRET in"}), 400
    return jsonify({"ok": True, "url": oauth.get_authorize_url()})


@app.route("/api/spotify/token", methods=["POST"])
def api_spotify_token():
    if not _unlocked():
        return jsonify({"ok": False, "error": "Niet ontgrendeld"}), 403
    oauth = _spotify_oauth()
    if not oauth:
        return jsonify({"ok": False, "error": "Spotify client-id/secret ontbreekt"}), 400
    redirect_url = (request.get_json(silent=True) or {}).get("redirect_url", "").strip()
    if not redirect_url:
        return jsonify({"ok": False, "error": "Plak de volledige URL waar je op uitkwam"}), 400
    try:
        code = oauth.parse_response_code(redirect_url)
        oauth.get_access_token(code, as_dict=False, check_cache=False)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400
    reset_services()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Data helpers — shared by the individual routes and by /api/overview so a
# slow-wifi client can fetch the whole overview in ONE request.
# --------------------------------------------------------------------------- #
def _system_stats() -> dict:
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


_health_cache: dict = {}          # name -> (ok, error, expires_at)
_HEALTH_TTL = 60                  # seconds


def _probe(name: str):
    """A cheap real check. Returns (ok: bool, error: str|None)."""
    s = svc(name)
    if s is None:
        return False, _errors.get(name, "niet beschikbaar")
    try:
        if name == "spotify":
            s.sp.current_user()          # fails fast if the token is dead
        elif name == "weer":
            if not s.fetch_weather():
                return False, "geen weerdata"
        elif name == "agenda":
            if getattr(s, "error", None):
                return False, s.error
            if not getattr(s, "calendars", None):
                return False, "geen agenda's gevonden"
        # radio: object exists == ok
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _service_status() -> dict:
    now = time.time()
    out = {}
    for n in ("spotify", "radio", "weer", "agenda"):
        cached = _health_cache.get(n)
        if not cached or cached[2] < now:
            ok, err = _probe(n)
            _health_cache[n] = (ok, err, now + _HEALTH_TTL)
        ok, err, _ = _health_cache[n]
        out[n] = {"ok": ok, "error": err}
    return out


def _weather_data(city=None) -> dict:
    w = svc("weer")
    if not w:
        return {"error": _errors.get("weer", "weer niet beschikbaar")}
    return w.fetch_weather(city) or {"error": "geen weerdata"}


def _calendar_today() -> dict:
    cal = svc("agenda")
    if not cal:
        return {"events": [], "error": _errors.get("agenda")}
    try:
        return {"events": cal.return_todays_events()}
    except Exception as exc:  # noqa: BLE001
        return {"events": [], "error": str(exc)}


@app.route("/api/system_stats")
def api_system_stats():
    data = _system_stats()
    return jsonify(data), (500 if "error" in data else 200)


@app.route("/api/service_status")
def api_service_status():
    return jsonify(_service_status())


@app.route("/api/weather")
def api_weather():
    data = _weather_data(request.args.get("city"))
    return jsonify(data), (503 if data.get("error") else 200)


@app.route("/api/calendar_today")
def api_calendar_today():
    return jsonify(_calendar_today())


@app.route("/api/overview")
def api_overview():
    """One round trip for the whole Overview page (saves 4 requests / cycle)."""
    from logic.notes import list_all_notes

    return jsonify({
        "system": _system_stats(),
        "services": _service_status(),
        "weather": _weather_data(),
        "calendar": _calendar_today(),
        "now_playing": _current_playing(),
        "notes": list_all_notes(),
    })


# --------------------------------------------------------------------------- #
# Notes API
# --------------------------------------------------------------------------- #
@app.route("/api/notes", methods=["GET"])
def api_notes_get():
    from logic.notes import list_all_notes

    return jsonify({"notes": list_all_notes()})


@app.route("/api/notes", methods=["POST"])
def api_notes_add():
    from logic.notes import add_note

    text = (request.get_json(silent=True) or {}).get("note", "").strip()
    if not text:
        return jsonify({"success": False, "error": "lege notitie"}), 400
    add_note(text)
    return jsonify({"success": True})


@app.route("/api/notes/<int:index>", methods=["DELETE"])
def api_notes_delete(index: int):
    from logic.notes import delete_note

    return jsonify({"success": delete_note(index)})


# --------------------------------------------------------------------------- #
# Notifications = recent log lines
# --------------------------------------------------------------------------- #
@app.route("/api/notifications")
def api_notifications():
    from datetime import datetime
    from pathlib import Path

    limit = int(request.args.get("limit", 40))
    base = Path(__file__).resolve().parent.parent.parent / "data" / "logs"
    lines: list[str] = []
    if base.is_dir():
        files = sorted(base.rglob("*.txt"))
        for fp in files[-4:]:
            try:
                lines.extend(fp.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass
    parsed = []
    for ln in lines[-limit:][::-1]:
        ln = ln.strip()
        if not ln:
            continue
        # format: [HH:MM:SS] [Subject] message
        subject, message, tijd = "", ln, ""
        if ln.startswith("[") and "]" in ln:
            try:
                tijd = ln[1:ln.index("]")]
                rest = ln[ln.index("]") + 1:].strip()
                if rest.startswith("[") and "]" in rest:
                    subject = rest[1:rest.index("]")]
                    message = rest[rest.index("]") + 1:].strip()
                else:
                    message = rest
            except ValueError:
                pass
        parsed.append({"time": tijd, "subject": subject, "message": message})
    return jsonify({"notifications": parsed, "date": datetime.now().strftime("%Y-%m-%d")})


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
@app.route("/api/send_message", methods=["POST"])
def api_send_message():
    text = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not text:
        return jsonify({"reply": "Typ iets alsjeblieft."})
    from logic.gpt_handler import verwerk_input

    return jsonify({"reply": verwerk_input(text)})


# --------------------------------------------------------------------------- #
# Routines
# --------------------------------------------------------------------------- #
@app.route("/api/routines")
def api_routines_list():
    return jsonify({"routines": [
        {"id": "morning", "name": "Ochtend-routine", "desc": "Lampen aan, radio, notities voorlezen"},
        {"id": "bedtime", "name": "Bedtijd-routine", "desc": "Lampen uit, welterusten, wekker zetten"},
        {"id": "party", "name": "Party-modus", "desc": "Flitsende kleuren op de lampen"},
        {"id": "desk", "name": "Bureau-stand", "desc": "Zacht warm licht om te werken"},
    ]})


@app.route("/api/routines/run", methods=["POST"])
def api_routines_run():
    rid = (request.get_json(silent=True) or {}).get("id")
    try:
        if rid == "morning":
            from scheduler.routines import morning_routine

            morning_routine()
        elif rid == "bedtime":
            from scheduler.routines import bedtime_routine

            bedtime_routine()
        elif rid in ("party", "desk"):
            ip = _lamp_ip(0)
            lamp = _lamp(ip)
            asyncio.run(lamp.party() if rid == "party" else lamp.bureau())
        else:
            return jsonify({"success": False, "error": "onbekende routine"}), 400
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


# --------------------------------------------------------------------------- #
# Media: Spotify + radio  (unchanged behaviour, hardened)
# --------------------------------------------------------------------------- #
def _sp():
    s = svc("spotify")
    if not s:
        raise RuntimeError(_errors.get("spotify", "Spotify niet beschikbaar"))
    return s


@app.route("/api/playlists")
def api_playlists():
    try:
        return jsonify(_sp().playlists_info(limit=10))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@app.route("/api/play_playlist", methods=["POST"])
def api_play_playlist():
    try:
        pid = (request.get_json(silent=True) or {}).get("id")
        if not pid:
            return jsonify({"success": False, "error": "Geen playlist ID"}), 400
        sp = _sp().sp
        sp.start_playback(context_uri=f"spotify:playlist:{pid}")
        return jsonify({"success": True, "name": sp.playlist(pid)["name"]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/playlist_tracks/<playlist_id>")
def api_playlist_tracks(playlist_id):
    try:
        sp = _sp().sp
        playlist = sp.playlist(playlist_id)
        tracks = [{
            "id": it["track"]["id"],
            "name": it["track"]["name"],
            "artist": ", ".join(a["name"] for a in it["track"]["artists"]),
            "duration": f"{int(it['track']['duration_ms']/60000)}:{int((it['track']['duration_ms']%60000)/1000):02d}",
            "thumbnail": it["track"]["album"]["images"][0]["url"] if it["track"]["album"]["images"] else "",
            "uri": it["track"]["uri"],
        } for it in playlist["tracks"]["items"] if it.get("track")]
        return jsonify({
            "name": playlist["name"],
            "thumbnail": playlist["images"][0]["url"] if playlist["images"] else "",
            "tracks": tracks,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/last_played_playlists")
def api_last_played_playlists():
    try:
        return jsonify(_sp().laatste_playlists(limit=30))
    except Exception as exc:  # noqa: BLE001
        return jsonify([]), 503


@app.route("/api/play_track", methods=["POST"])
def api_play_track():
    try:
        data = request.get_json(silent=True) or {}
        track_id = data.get("track_id")
        playlist_id = data.get("playlist_id")
        if not track_id:
            return jsonify({"success": False, "error": "track_id ontbreekt"}), 400
        sp = _sp().sp
        dev = sp.current_playback()
        device_id = dev["device"]["id"] if dev and dev.get("device") else None
        if playlist_id:
            items = sp.playlist(playlist_id)["tracks"]["items"]
            offset = next((i for i, it in enumerate(items) if it["track"]["id"] == track_id), 0)
            sp.start_playback(device_id=device_id, context_uri=f"spotify:playlist:{playlist_id}", offset={"position": offset})
        else:
            sp.start_playback(device_id=device_id, uris=[f"spotify:track:{track_id}"])
        return jsonify({"success": True, "track_id": track_id})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/search_spotify")
def api_search_spotify():
    query = request.args.get("query", "")
    if not query:
        return jsonify({"tracks": []})
    try:
        results = _sp().sp.search(q=query, type="track", limit=10)
        return jsonify({"tracks": [{
            "id": it["id"],
            "name": it["name"],
            "artist": ", ".join(a["name"] for a in it["artists"]),
            "album": it["album"]["name"],
            "thumbnail": it["album"]["images"][0]["url"] if it["album"]["images"] else "",
            "uri": it["uri"],
        } for it in results["tracks"]["items"]]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"tracks": [], "error": str(exc)}), 503


@app.route("/api/spotify_pause", methods=["POST"])
def api_spotify_pause():
    try:
        _sp().pauze()
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/spotify_resume", methods=["POST"])
def api_spotify_resume():
    try:
        _sp().resume()
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/spotify_next", methods=["POST"])
def api_spotify_next():
    try:
        _sp().sp.next_track()
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/spotify_previous", methods=["POST"])
def api_spotify_previous():
    try:
        _sp().sp.previous_track()
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/seek", methods=["POST"])
def api_seek():
    pos = (request.get_json(silent=True) or {}).get("position_ms")
    if pos is None:
        return jsonify({"success": False, "error": "Geen positie"}), 400
    try:
        _sp().sp.seek_track(int(pos))
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/devices")
def api_devices():
    try:
        devs = _sp().sp.devices()["devices"]
        return jsonify({"success": True, "devices": [
            {"id": d["id"], "name": d["name"], "type": d["type"], "active": d["is_active"]}
            for d in devs
        ]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 503


@app.route("/api/set_device", methods=["POST"])
def api_set_device():
    did = (request.get_json(silent=True) or {}).get("device_id")
    if not did:
        return jsonify({"success": False, "error": "Geen device ID"}), 400
    try:
        _sp().sp.transfer_playback(device_id=did, force_play=False)
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


# ---- Radio ----
@app.route("/api/radio_stations")
def api_radio_stations():
    r = svc("radio")
    return jsonify([{"name": n} for n in (r.stations.keys() if r else [])])


@app.route("/api/radio_play", methods=["POST"])
def api_radio_play():
    station = (request.get_json(silent=True) or {}).get("station")
    r = svc("radio")
    if not r:
        return jsonify({"success": False, "error": "radio niet beschikbaar"}), 503
    if not station:
        return jsonify({"success": False, "error": "Geen station"}), 400
    return jsonify({"success": True, "message": r.play(station)})


@app.route("/api/radio_stop", methods=["POST"])
def api_radio_stop():
    r = svc("radio")
    return jsonify({"success": True, "message": r.stop() if r else "radio niet beschikbaar"})


@app.route("/api/radio_pause", methods=["POST"])
def api_radio_pause():
    r = svc("radio")
    return jsonify({"success": True, "message": r.pause() if r else "-"})


@app.route("/api/radio_resume", methods=["POST"])
def api_radio_resume():
    r = svc("radio")
    return jsonify({"success": True, "message": r.resume() if r else "-"})


# ---- Combined now-playing ----
def _current_playing() -> dict:
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


@app.route("/api/current_playing")
def api_current_playing():
    return jsonify(_current_playing())


@app.route("/api/set_volume", methods=["POST"])
def api_set_volume():
    volume = (request.get_json(silent=True) or {}).get("volume")
    if volume is None:
        return jsonify({"success": False, "error": "Geen volume"}), 400
    sp, r = svc("spotify"), svc("radio")
    try:
        if sp and sp.current_track().get("type") == "spotify":
            ok = sp.set_volume(volume)
        elif r and r.current_station().get("type") == "radio":
            ok = r.set_volume(volume)
        else:
            return jsonify({"success": False, "error": "Niets speelt"}), 400
        return jsonify({"success": bool(ok)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500


# --------------------------------------------------------------------------- #
# Lamps
# --------------------------------------------------------------------------- #
def _lamp_action(coro_factory, lamp_ref=None):
    if lamp_ref is None:
        lamp_ref = (request.get_json(silent=True) or {}).get("lamp", 0)
    ip = _lamp_ip(lamp_ref)
    if not ip:
        return jsonify({"status": "error", "message": "geen lamp geconfigureerd"}), 400
    try:
        return jsonify({"status": "ok", "message": asyncio.run(coro_factory(_lamp(ip)))})
    except Exception as exc:  # noqa: BLE001
        _lamp_conns.pop(ip, None)  # gooi stale verbinding weg
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/lamp/on", methods=["PUT"])
def lamp_on():
    return _lamp_action(lambda l: l.aan())


@app.route("/api/lamp/off", methods=["PUT"])
def lamp_off():
    return _lamp_action(lambda l: l.uit())


@app.route("/api/lamp/brightness", methods=["PUT"])
def lamp_brightness():
    b = int((request.get_json(silent=True) or {}).get("brightness", 100))
    return _lamp_action(lambda l: l.zet_helderheid(b))


@app.route("/api/lamp/color", methods=["PUT"])
def lamp_color():
    c = (request.get_json(silent=True) or {}).get("color")
    return _lamp_action(lambda l: l.zet_kleur(c))


@app.route("/api/lamp/colortemp", methods=["PUT"])
def lamp_colortemp():
    t = int((request.get_json(silent=True) or {}).get("color_temp", 4000))
    return _lamp_action(lambda l: l.zet_kleur_temp(t))


@app.route("/api/lamp/mode/<mode>", methods=["POST"])
def lamp_mode(mode):
    factory = {
        "party": lambda l: l.party(duur=10, interval=0.2),
        "desk": lambda l: l.bureau(),
        "normal": lambda l: l.normaal(),
    }.get(mode)
    if not factory:
        return jsonify({"status": "error", "message": "onbekende modus"}), 400
    return _lamp_action(factory)


@app.route("/api/lamps")
def api_lamps():
    return jsonify({"lamps": config.get("devices.lamps", [])})


@app.route("/api/thermostat", methods=["GET", "POST"])
def api_thermostat():
    from devices.thermostat import ThermostatController

    tc = ThermostatController()
    if request.method == "POST":
        target = (request.get_json(silent=True) or {}).get("target")
        if target is None:
            return jsonify({"success": False, "error": "geen target"}), 400
        try:
            tc.set_temperature(target)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "ongeldige waarde"}), 400
    return jsonify(tc.state())


@app.route("/api/lamp/state")
def api_lamp_state():
    ip = _lamp_ip(request.args.get("lamp", 0))
    if not ip:
        return jsonify({"ok": False, "error": "geen lamp"}), 400
    try:
        return jsonify({"ok": True, "state": asyncio.run(_lamp(ip).status())})
    except Exception as exc:  # noqa: BLE001
        _lamp_conns.pop(ip, None)
        return jsonify({"ok": False, "error": str(exc)}), 503


# --------------------------------------------------------------------------- #
# Camera — one capture thread feeds every viewer the latest JPEG. This keeps
# CPU flat no matter how many browser tabs are open, and the capture stops on
# its own when nobody is watching.
# --------------------------------------------------------------------------- #
class _Camera:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._latest = None          # (jpeg_bytes, seq)
        self._seq = 0
        self._viewers = 0
        self._stop = threading.Event()
        self.error = None

    def _run(self):
        try:
            import cv2
        except Exception as exc:  # noqa: BLE001
            self.error = f"opencv ontbreekt: {exc}"
            return
        idx = config.get("camera.device_index", 0)
        cap = cv2.VideoCapture(idx)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.get("camera.width", 640))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.get("camera.height", 360))
        cap.set(cv2.CAP_PROP_FPS, config.get("camera.fps", 10))
        if not cap.isOpened():
            self.error = f"camera {idx} kan niet worden geopend"
            cap.release()
            return
        self.error = None
        w, h = config.get("camera.width", 640), config.get("camera.height", 360)
        try:
            while not self._stop.is_set():
                q = int(config.get("camera.jpeg_quality", 55))
                delay = 1.0 / max(1, config.get("camera.fps", 10))
                ok, frame = cap.read()
                if not ok:
                    self.error = "geen beeld van camera"
                    break
                frame = cv2.resize(frame, (w, h))
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), q])
                if ok:
                    with self._lock:
                        self._seq += 1
                        self._latest = (buf.tobytes(), self._seq)
                time.sleep(delay)
        finally:
            cap.release()
            with self._lock:
                self._latest = None

    def _ensure_running(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def release(self):
        self._stop.set()

    def frames(self):
        self._ensure_running()
        with self._lock:
            self._viewers += 1
        last = 0
        idle = 0
        try:
            while not self._stop.is_set():
                with self._lock:
                    latest = self._latest
                if latest and latest[1] != last:
                    last = latest[1]
                    idle = 0
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + latest[0] + b"\r\n")
                else:
                    idle += 1
                    if idle > 50 and self.error:   # ~5s zonder frames + fout -> stoppen
                        break
                time.sleep(1.0 / max(1, config.get("camera.fps", 10)))
        finally:
            with self._lock:
                self._viewers -= 1
                if self._viewers <= 0:
                    self._stop.set()


_camera = _Camera()


@app.route("/video_feed")
def video_feed():
    if not config.get("camera.enabled", True):
        return Response("camera uit", status=503)
    return Response(
        _camera.frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


@app.route("/api/camera_status")
def api_camera_status():
    return jsonify({
        "enabled": bool(config.get("camera.enabled", True)),
        "error": _camera.error,
        "viewers": _camera._viewers,
    })
