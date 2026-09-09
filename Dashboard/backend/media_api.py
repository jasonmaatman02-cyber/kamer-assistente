"""Spotify + radio + now-playing."""
from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S

media_bp = Blueprint("media", __name__)


def _no_device_response():
    return jsonify({
        "success": False,
        "no_device": True,
        "error": "Geen actief Spotify-apparaat. Open Spotify op je telefoon, pc of box "
                 "(speel daar even iets) en kies het apparaat in de lijst.",
    }), 409


def _start_playback(sp, **kwargs):
    """Speel af op het beste apparaat.

    - Is er een apparaat mét id -> dat expliciet wekken en gebruiken.
    - Geen id (bv. een Sonos die via z'n eigen Spotify speelt) -> zónder
      device_id afspelen; Spotify stuurt het dan naar het actieve apparaat
      (dat kan die Sonos zijn). Zoals de oude versie het deed.
    - Vindt Spotify écht niks -> NO_ACTIVE_DEVICE, afgevangen door _play_error.
    """
    dev = S.active_device_id(sp)
    if dev:
        S.wake_device(sp, dev)
        kwargs["device_id"] = dev
    sp.start_playback(**kwargs)


def _play_error(exc):
    msg = str(getattr(exc, "msg", "") or exc)
    if "NO_ACTIVE_DEVICE" in msg or "No active device" in msg:
        return _no_device_response()
    if "Restriction violated" in msg:
        return jsonify({"success": False, "error": "Spotify weigerde dit commando (niets speelt of apparaat staat het niet toe)."}), 403
    if "invalid_grant" in msg:
        return jsonify({"success": False, "relink": True, "error": "Spotify-token verlopen — opnieuw koppelen via Settings."}), 401
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
    sp = S.sp_dj().sp
    try:
        meta = sp.playlist(playlist_id, fields="name,images")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

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
    pid = (request.get_json(silent=True) or {}).get("id")
    if not pid:
        return jsonify({"success": False, "error": "Geen playlist ID"}), 400
    try:
        _start_playback(S.sp_dj().sp, context_uri=f"spotify:playlist:{pid}")
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return _play_error(exc)


@media_bp.route("/api/play_track", methods=["POST"])
def play_track():
    data = request.get_json(silent=True) or {}
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
        fn()
        return jsonify({"success": True})
    except Exception as exc:  # noqa: BLE001
        return _play_error(exc)


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
    pos = (request.get_json(silent=True) or {}).get("position_ms")
    if pos is None:
        return jsonify({"success": False, "error": "Geen positie"}), 400
    return _simple(lambda: S.sp_dj().sp.seek_track(int(pos)))


@media_bp.route("/api/devices")
def devices():
    try:
        sp = S.sp_dj().sp
    except Exception as exc:  # noqa: BLE001 - geen Spotify-koppeling
        return jsonify({"success": False, "error": str(exc)}), 503

    devs: dict = {}
    errors = []
    # sp.devices() laat een Sonos/SYMFONISK vaak weg, óók terwijl 'ie speelt...
    try:
        for d in (sp.devices().get("devices") or []):
            if d.get("id"):
                devs[d["id"]] = d
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    # ...maar current_playback() kent 'm wel. Een Sonos/Cast krijgt van Spotify
    # geen id (je kunt er niet via de Web-API naartoe schakelen) -> toch tonen,
    # maar als 'speelt hier', niet als kies-doel.
    playing = None
    try:
        dev = (sp.current_playback() or {}).get("device") or {}
        if dev.get("id"):
            devs.setdefault(dev["id"], dev)
        elif dev.get("name"):
            playing = {"id": None, "name": dev["name"], "type": dev.get("type", "Speaker"), "active": True}
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    if not devs and not playing and errors:
        return jsonify({"success": False, "error": errors[0]}), 503
    out = [{"id": d["id"], "name": d.get("name", "?"), "type": d.get("type", "?"),
            "active": bool(d.get("is_active"))} for d in devs.values()]
    if playing:
        out.append(playing)
    return jsonify({"success": True, "warning": errors[0] if errors else None, "devices": out})


@media_bp.route("/api/set_device", methods=["POST"])
def set_device():
    did = (request.get_json(silent=True) or {}).get("device_id")
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
    station = (request.get_json(silent=True) or {}).get("station")
    r = S.svc("radio")
    if not r:
        return jsonify({"success": False, "error": "radio niet beschikbaar"}), 503
    if not station:
        return jsonify({"success": False, "error": "Geen station"}), 400
    return jsonify({"success": True, "message": r.play(station)})


@media_bp.route("/api/radio_stop", methods=["POST"])
def radio_stop():
    r = S.svc("radio")
    return jsonify({"success": True, "message": r.stop() if r else "radio niet beschikbaar"})


@media_bp.route("/api/radio_pause", methods=["POST"])
def radio_pause():
    r = S.svc("radio")
    return jsonify({"success": True, "message": r.pause() if r else "-"})


@media_bp.route("/api/radio_resume", methods=["POST"])
def radio_resume():
    r = S.svc("radio")
    return jsonify({"success": True, "message": r.resume() if r else "-"})


# --------------------------------------------------------------------------- #
# Now playing / volume
# --------------------------------------------------------------------------- #
@media_bp.route("/api/current_playing")
def current_playing():
    return jsonify(S.current_playing())


@media_bp.route("/api/set_volume", methods=["POST"])
def set_volume():
    volume = (request.get_json(silent=True) or {}).get("volume")
    if volume is None:
        return jsonify({"success": False, "error": "Geen volume"}), 400
    sp, r = S.svc("spotify"), S.svc("radio")
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
