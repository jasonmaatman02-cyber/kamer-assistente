"""Spotify + radio + now-playing."""
from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S
from Dashboard.backend.util import json_body

media_bp = Blueprint("media", __name__)


def _no_device_response():
    return jsonify({
        "success": False,
        "no_device": True,
        "error": "Geen actief Spotify-apparaat. Open Spotify op je telefoon, pc of box "
                 "(speel daar even iets) en kies het apparaat in de lijst.",
    }), 409


def _start_playback(sp, **kwargs):
    """Dunne wrapper -- zie services.start_playback() voor de logica
    (gedeeld met sound_system/muziek.py::speel_muziek(), zodat dashboard-
    knoppen en spraak/AI-commando's hetzelfde apparaat-standaardgedrag
    hebben)."""
    S.start_playback(sp, **kwargs)


def _play_error(exc):
    msg = str(getattr(exc, "msg", "") or exc)
    if "NO_ACTIVE_DEVICE" in msg or "No active device" in msg:
        return _no_device_response()
    if "Restriction violated" in msg:
        return jsonify({"success": False, "error": "Spotify weigerde dit commando (niets speelt of apparaat staat het niet toe)."}), 403
    if "invalid_grant" in msg:
        return jsonify({"success": False, "relink": True, "error": "Spotify-token verlopen — opnieuw koppelen via Settings."}), 401
    if isinstance(exc, RuntimeError):
        # 'Spotify niet ingesteld / niet beschikbaar' (S.sp_dj()): de dienst ontbreekt,
        # dat is geen serverfout.
        return jsonify({"success": False, "error": msg}), 503
    return jsonify({"success": False, "error": msg}), 500


# --------------------------------------------------------------------------- #
# Playlists
# --------------------------------------------------------------------------- #
@media_bp.route("/api/playlists")
def playlists():
    try:
        return jsonify(S.sp_dj().playlists_info(limit=12))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503


@media_bp.route("/api/last_played_playlists")
def last_played_playlists():
    try:
        return jsonify(S.sp_dj().laatste_playlists(limit=30))
    except Exception:  # noqa: BLE001
        return jsonify([]), 503


@media_bp.route("/api/playlist_tracks/<playlist_id>")
def playlist_tracks(playlist_id):
    try:
        sp = S.sp_dj().sp       # buiten de try gaf 'Spotify niet ingesteld' een HTML-500
        meta = sp.playlist(playlist_id, fields="name,images")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), (503 if isinstance(exc, RuntimeError) else 500)

    market = config.get("spotify.market", "NL")
    tracks, offset = [], 0
    try:
        while True:
            page = sp.playlist_items(playlist_id, market=market, additional_types=("track",),
                                     limit=100, offset=offset)
            for it in page.get("items", []):
                if not isinstance(it, dict):
                    continue
                t = it.get("item") or it.get("track")   # nieuw formaat: "item"
                if not isinstance(t, dict) or t.get("type") == "episode" or not t.get("uri"):
                    continue
                ms = t.get("duration_ms", 0) or 0
                imgs = (t.get("album") or {}).get("images") or []
                tracks.append({
                    "id": t.get("id"),
                    "name": t.get("name", "?"),
                    "artist": ", ".join(a["name"] for a in t.get("artists", []) if a.get("name")),
                    "duration": f"{ms // 60000}:{(ms % 60000) // 1000:02d}",
                    "thumbnail": imgs[0]["url"] if imgs else "",
                    "uri": t["uri"],
                })
            if not page.get("next") or len(tracks) >= 300:
                break
            offset += 100
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    imgs = meta.get("images") or []
    return jsonify({"name": meta.get("name", "Playlist"),
                    "thumbnail": imgs[0]["url"] if imgs else "", "tracks": tracks})


# --------------------------------------------------------------------------- #
# Playback
# --------------------------------------------------------------------------- #
@media_bp.route("/api/play_playlist", methods=["POST"])
def play_playlist():
    pid = json_body().get("id")
    if not pid:
        return jsonify({"success": False, "error": "Geen playlist ID"}), 400
    try:
        _start_playback(S.sp_dj().sp, context_uri=f"spotify:playlist:{pid}")
        S.invalidate("now_playing", "spotify:devices")
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return _play_error(exc)


@media_bp.route("/api/play_track", methods=["POST"])
def play_track():
    data = json_body()
    track_id = data.get("track_id")
    playlist_id = data.get("playlist_id")
    if not track_id:
        return jsonify({"success": False, "error": "track_id ontbreekt"}), 400
    try:
        sp = S.sp_dj().sp
        track_uri = f"spotify:track:{track_id}"
        if playlist_id:
            _start_playback(sp, context_uri=f"spotify:playlist:{playlist_id}", offset={"uri": track_uri})
        else:
            try:
                album_uri = sp.track(track_uri).get("album", {}).get("uri")
            except Exception:  # noqa: BLE001
                album_uri = None
            if album_uri:
                _start_playback(sp, context_uri=album_uri, offset={"uri": track_uri})
            else:
                _start_playback(sp, uris=[track_uri])
        S.invalidate("now_playing", "spotify:devices")
        return jsonify({"success": True, "track_id": track_id})
    except Exception as exc:  # noqa: BLE001
        return _play_error(exc)


@media_bp.route("/api/search_spotify")
def search_spotify():
    query = request.args.get("query", "")
    if not query:
        return jsonify({"tracks": []})
    try:
        results = S.sp_dj().sp.search(q=query, type="track", limit=10,
                                      market=config.get("spotify.market", "NL"))
        return jsonify({"tracks": [{
            "id": it["id"], "name": it["name"],
            "artist": ", ".join(a["name"] for a in it["artists"]),
            "album": it["album"]["name"],
            "thumbnail": it["album"]["images"][0]["url"] if it["album"]["images"] else "",
            "uri": it["uri"],
        } for it in results["tracks"]["items"]]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"tracks": [], "error": str(exc)}), 503


def _simple(fn):
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        return _play_error(exc)
    if result is False:
        # SpotifyDJ._call() vangt de fout zelf af en geeft False terug -- dat werd
        # hier als succes gemeld (pauze/hervat zonder actief apparaat leek te
        # lukken, de reden bereikte de UI nooit).
        reason = getattr(S.svc("spotify"), "last_error", None) or "Spotify-commando mislukt"
        if "Geen actief" in reason:
            return _no_device_response()
        return jsonify({"success": False, "error": reason}), 502
    S.invalidate("now_playing", "spotify:devices")   # UI toont meteen de nieuwe staat
    return jsonify({"success": True})


@media_bp.route("/api/spotify_pause", methods=["POST"])
def spotify_pause():
    return _simple(lambda: S.sp_dj().pauze())


@media_bp.route("/api/spotify_resume", methods=["POST"])
def spotify_resume():
    return _simple(lambda: S.sp_dj().resume())


@media_bp.route("/api/spotify_next", methods=["POST"])
def spotify_next():
    return _simple(lambda: S.sp_dj().sp.next_track())


@media_bp.route("/api/spotify_previous", methods=["POST"])
def spotify_previous():
    return _simple(lambda: S.sp_dj().sp.previous_track())


@media_bp.route("/api/seek", methods=["POST"])
def seek():
    pos = json_body().get("position_ms")
    if pos is None:
        return jsonify({"success": False, "error": "Geen positie"}), 400
    try:
        pos = max(0, int(pos))
    except (TypeError, ValueError, OverflowError):
        return jsonify({"success": False, "error": "positie moet een getal zijn (ms)"}), 400
    return _simple(lambda: S.sp_dj().sp.seek_track(pos))


@media_bp.route("/api/devices")
def devices():
    data = S.spotify_device_list()
    return jsonify(data), (200 if data.get("success") else 503)


@media_bp.route("/api/set_device", methods=["POST"])
def set_device():
    did = json_body().get("device_id")
    if not did:
        return jsonify({"success": False, "error": "Geen device ID"}), 400
    return _simple(lambda: S.sp_dj().sp.transfer_playback(device_id=did, force_play=False))


# --------------------------------------------------------------------------- #
# Radio
# --------------------------------------------------------------------------- #
@media_bp.route("/api/radio_stations")
def radio_stations():
    r = S.svc("radio")
    return jsonify([{"name": n} for n in (r.stations.keys() if r else [])])


@media_bp.route("/api/radio_play", methods=["POST"])
def radio_play():
    station = json_body().get("station")
    r = S.svc("radio")
    if not r:
        return jsonify({"success": False, "error": "radio niet beschikbaar"}), 503
    if not station or not isinstance(station, str):
        return jsonify({"success": False, "error": "Geen station"}), 400
    msg = r.play(station)
    S.invalidate("now_playing")
    err = getattr(r, "last_error", None)
    if err:
        # onbekend station -> 404; stream startte niet (URL/internet) -> 502
        return jsonify({"success": False, "error": err}), (404 if "niet gevonden" in err else 502)
    return jsonify({"success": True, "message": msg})


@media_bp.route("/api/radio_stop", methods=["POST"])
def radio_stop():
    r = S.svc("radio")
    if not r:
        return jsonify({"success": False, "error": "radio niet beschikbaar"}), 503
    msg = r.stop()
    S.invalidate("now_playing")
    return jsonify({"success": True, "message": msg})


@media_bp.route("/api/radio_pause", methods=["POST"])
def radio_pause():
    r = S.svc("radio")
    if not r:
        return jsonify({"success": False, "error": "radio niet beschikbaar"}), 503
    msg = r.pause()
    S.invalidate("now_playing")
    return jsonify({"success": True, "message": msg})


@media_bp.route("/api/radio_resume", methods=["POST"])
def radio_resume():
    r = S.svc("radio")
    if not r:
        return jsonify({"success": False, "error": "radio niet beschikbaar"}), 503
    msg = r.resume()
    S.invalidate("now_playing")
    return jsonify({"success": True, "message": msg})


# --------------------------------------------------------------------------- #
# Now playing / volume
# --------------------------------------------------------------------------- #
@media_bp.route("/api/current_playing")
def current_playing():
    return jsonify(S.current_playing())


@media_bp.route("/api/set_volume", methods=["POST"])
def set_volume():
    volume = json_body().get("volume")
    if volume is None:
        return jsonify({"success": False, "error": "Geen volume"}), 400
    try:
        volume = max(0, min(100, int(volume)))
    except (TypeError, ValueError, OverflowError):
        return jsonify({"success": False, "error": "volume moet een getal zijn (0-100)"}), 400
    sp, r = S.svc("spotify"), S.svc("radio")
    try:
        if sp and sp.current_track().get("type") == "spotify":
            ok = sp.set_volume(volume)
        elif r and r.current_station().get("type") == "radio":
            ok = r.set_volume(volume)
        else:
            return jsonify({"success": False, "error": "Niets speelt"}), 400
        S.invalidate("now_playing")
        return jsonify({"success": bool(ok)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500
