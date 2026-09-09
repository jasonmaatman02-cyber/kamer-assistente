from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth

import config
from logic.logger import log

_CACHE_PATH = str(Path(__file__).resolve().parent.parent / ".cache")

# Één plek voor de scope — de OAuth-koppelknop en SpotifyDJ MOETEN gelijk zijn,
# anders vindt spotipy het gecachte token "te weinig" en probeert opnieuw in te
# loggen (wat op een headless Pi de request kan laten hangen).
SPOTIFY_SCOPE = (
    "user-read-playback-state user-modify-playback-state "
    "user-read-recently-played playlist-read-private"
)


class SpotifyError(RuntimeError):
    pass


class SpotifyDJ:
    def __init__(self):
        self.scope = SPOTIFY_SCOPE
        # requests_timeout blokkeert een eindeloze hang; retries op de default (3)
        # laten -> een Sonos/SYMFONISK die Spotify traag opsomt (kort 503) valt
        # anders uit sp.devices()
        self.sp = spotipy.Spotify(
            requests_timeout=10,
            auth_manager=SpotifyOAuth(
                client_id=config.secret("SPOTIFY_CLIENT_ID"),
                client_secret=config.secret("SPOTIFY_CLIENT_SECRET"),
                redirect_uri=config.secret("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback"),
                scope=self.scope,
                open_browser=False,
                cache_path=_CACHE_PATH,
            ),
        )

    # ------------------------------------------------------------------ #
    def _call(self, what, fn, *a, **kw):
        try:
            fn(*a, **kw)
            return True
        except spotipy.SpotifyException as exc:
            msg = "Geen actief Spotify-apparaat." if "NO_ACTIVE_DEVICE" in str(exc) or exc.http_status == 404 \
                else f"Spotify-fout: {exc.msg or exc}"
            log("Muziek", f"{what} mislukt: {exc}")
            print(msg)
            return False
        except Exception as exc:  # noqa: BLE001
            log("Muziek", f"{what} mislukt: {exc}")
            print(f"Spotify onbereikbaar: {exc}")
            return False

    # ------------------------------------------------------------------ #
    def speel_muziek(self, zoekterm: str):
        try:
            result = self.sp.search(q=zoekterm, type="track", limit=1)
        except Exception as exc:  # noqa: BLE001
            log("Muziek", f"zoeken mislukt: {exc}")
            return False
        items = result.get("tracks", {}).get("items") or []
        if not items:
            return False
        track = items[0]
        ok = self._call("afspelen", self.sp.start_playback, uris=[track["uri"]])
        if ok:
            print(f"Afspelen gestart: {track['name']} van {track['artists'][0]['name']}")
        return ok

    def pauze(self):
        return self._call("pauze", self.sp.pause_playback)

    def resume(self):
        return self._call("hervat", self.sp.start_playback)

    def stop(self):
        return self._call("stop", self.sp.pause_playback)

    def set_volume(self, volume: int):
        volume = max(0, min(100, int(volume)))
        return self._call("volume", self.sp.volume, volume)

    # ------------------------------------------------------------------ #
    def laatst_afgespeeld(self, limit=5):
        try:
            results = self.sp.current_user_recently_played(limit=limit)
        except Exception:  # noqa: BLE001
            return []
        return [
            f"{it['track']['name']} van {it['track']['artists'][0]['name']}"
            for it in results.get("items", [])
        ]

    def laatste_playlists(self, limit=5):
        try:
            results = self.sp.current_user_recently_played(limit=50)
        except Exception:  # noqa: BLE001
            return []
        seen, out = set(), []
        for item in results.get("items", []):
            ctx = item.get("context")
            if not ctx or ctx.get("type") != "playlist" or ctx["uri"] in seen:
                continue
            seen.add(ctx["uri"])
            try:
                p = self.sp.playlist(ctx["uri"])
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(p, dict) or not p.get("id"):
                continue
            images = p.get("images") or []
            out.append({
                "naam": p.get("name", "Naamloos"),
                "id": p["id"],
                "tracks_count": (p.get("tracks") or {}).get("total", 0),
                "thumbnail": images[0]["url"] if images else None,
            })
            if len(out) >= limit:
                break
        return out

    def playlists_info(self, limit=40, with_duration=False):
        """Playlist-overzicht. ``with_duration`` doet een extra API-call per
        playlist voor de totale speelduur — standaard uit (scheelt N calls).
        Gooit een SpotifyError zodat de UI de reden kan tonen."""
        try:
            results = self.sp.current_user_playlists(limit=limit)
        except spotipy.SpotifyException as exc:
            raise SpotifyError(exc.msg or str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise SpotifyError(str(exc)) from exc
        out = []
        for p in results.get("items", []):
            if not isinstance(p, dict) or not p.get("id"):
                continue  # Spotify geeft soms lege/onvolledige playlist-items terug
            images = p.get("images") or []
            tracks = p.get("tracks") or {}
            duur = ""
            if with_duration:
                try:
                    pt = self.sp.playlist_tracks(p["id"])
                    ms = sum(t["track"]["duration_ms"] for t in pt.get("items", []) if t.get("track"))
                    duur = f"{ms // 60000}m {(ms % 60000) // 1000}s"
                except Exception:  # noqa: BLE001
                    duur = ""
            out.append({
                "naam": p.get("name", "Naamloos"),
                "tracks_count": tracks.get("total", 0),
                "thumbnail": images[0]["url"] if images else None,
                "duur": duur,
                "id": p["id"],
            })
        return out

    def current_track(self):
        try:
            playback = self.sp.current_playback()
            if not playback or not playback.get("item"):
                return {"type": "none", "info": "Geen muziek aan het spelen"}
            track = playback["item"]
            imgs = track.get("album", {}).get("images") or []
            progress_ms = playback.get("progress_ms", 0)
            duration_ms = track["duration_ms"]
            return {
                "type": "spotify",
                "name": track["name"],
                "artist": track["artists"][0]["name"] if track.get("artists") else "",
                "album": track.get("album", {}).get("name", ""),
                "thumbnail": imgs[0]["url"] if imgs else "",
                "progress_ms": progress_ms,
                "duration_ms": duration_ms,
                "remaining_ms": duration_ms - progress_ms,
                "is_playing": playback["is_playing"],
                "volume": (playback.get("device") or {}).get("volume_percent", 50),
            }
        except Exception as e:  # noqa: BLE001
            return {"type": "error", "info": str(e)}
