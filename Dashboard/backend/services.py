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
_lamp_fail_at: dict = {}          # ip -> _mono()-tijd van de laatste MISLUKTE connect
_mono = time.monotonic            # seam voor tests
# Direct na een mislukte connect (lamp offline) zo lang meteen falen i.p.v.
# elke wachtende poll opnieuw een eigen (trage) connect te laten doen: zonder
# dit stapelden devices.js-polls zich achter de per-lamp-lock op tot alle 16
# waitress-workers vastzaten en het HELE dashboard bevroor (lokaal
# gereproduceerd: 2 offline lampen + 2 open tabbladen). Bewust korter dan de
# presence-interval (3s) zodat presence-retries wél echt opnieuw proberen.
_LAMP_FAIL_TTL_S = 2.0
_LAMP_LOCK_WAIT_S = 15.0          # langer wachten op de connect-lock heeft geen zin
_health_cache: dict = {}          # name -> (ok, error, expires_at)
_data_cache: dict = {}            # key -> (value, expires_at) — weer/agenda voor /api/overview
_HEALTH_TTL = 60
_locks_lock = threading.Lock()    # beschermt _build_locks / _lamp_locks zelf
_build_locks: dict = {}           # name -> Lock (per service, zodat er 4 tegelijk kunnen bouwen)
_data_cache_locks: dict = {}      # key -> Lock (voorkomt cache-stampede in _cached())


def _named_lock(store: dict, key: str) -> threading.Lock:
    with _locks_lock:
        return store.setdefault(key, threading.Lock())


def _build(name: str):
    if name == "spotify":
        if not (config.secret("SPOTIFY_CLIENT_ID") and config.secret("SPOTIFY_CLIENT_SECRET")):
            # i.p.v. spotipy's kale "No client_id. Pass it or set a
            # SPOTIPY_CLIENT_ID environment variable." (stond zo in de Media-UI)
            raise RuntimeError("Spotify is nog niet ingesteld -- vul Client ID en Secret in bij "
                               "Settings > Inloggegevens en koppel daarna Spotify.")
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
    """Return a shared service instance, or ``None`` if it can't be created.
    Per service een eigen bouw-lock: gelijktijdige requests voor verschillende
    diensten (bv. de parallelle /api/overview-probes) bouwen wél tegelijk."""
    if name in _services:            # snelle weg, geen lock nodig
        return _services[name]
    with _named_lock(_build_locks, name):
        if name in _services:
            return _services[name]
        try:
            built = _build(name)
        except KeyError:
            return None
        except Exception as exc:  # noqa: BLE001 - één storing mag niet het hele dashboard slopen
            first_time = _errors.get(name) != str(exc)
            _errors[name] = str(exc)
            _services[name] = None
            print(f"[dashboard] service '{name}' niet beschikbaar: {exc}")
            if first_time:  # niet elke poll opnieuw in het logboek spammen
                log("Service", f"{name} niet beschikbaar: {exc}")
            return None
        _services[name] = built
        _errors.pop(name, None)
        return built


def errors() -> dict:
    return _errors


def reset_services():
    """Called after a settings/secret change so new config takes effect."""
    with _locks_lock:
        _services.clear()
        _errors.clear()
        _health_cache.clear()
        _data_cache.clear()
        _lamp_conns.clear()
        _lamp_locks.clear()
        _lamp_fail_at.clear()
        _build_locks.clear()
        _data_cache_locks.clear()
    try:
        from Dashboard.backend.camera_api import camera

        camera.release()
    except Exception as exc:  # noqa: BLE001 - reload mag hier niet op stuk lopen
        print(f"[services] camera.release bij reset: {exc!r}")


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
def _connect_lamp_locked(ip: str):
    """Bouw + connect een SlimmeLamp. Aanroeper houdt de per-IP lock vast."""
    from devices.Lights import SlimmeLamp

    obj = SlimmeLamp(config.secret("TAPO_USER"), config.secret("TAPO_PASSWORD"), ip)
    try:
        asyncio.run(obj.connect())
    except Exception:
        _lamp_fail_at[ip] = _mono()
        raise
    _lamp_fail_at.pop(ip, None)
    _lamp_conns[ip] = obj
    return obj


def _acquire_lamp_lock(ip: str) -> threading.Lock:
    lock = _named_lock(_lamp_locks, ip)
    if not lock.acquire(timeout=_LAMP_LOCK_WAIT_S):
        raise RuntimeError(f"lamp {ip}: verbinding is bezet, probeer het zo opnieuw")
    return lock


def lamp(ip: str):
    """Return a connected SlimmeLamp for ``ip``, reusing the connection.
    Eén verbindingspoging per lamp tegelijk (connect() is een trage netwerkcall);
    wachten op die lock is begrensd en een net-mislukte connect wordt niet
    door elke wachtende thread opnieuw geprobeerd (zie _LAMP_FAIL_TTL_S)."""
    existing = _lamp_conns.get(ip)
    if existing is not None and existing.lamp is not None:
        return existing
    lock = _acquire_lamp_lock(ip)
    try:
        existing = _lamp_conns.get(ip)
        if existing is not None and existing.lamp is not None:
            return existing
        failed_at = _lamp_fail_at.get(ip)
        if failed_at is not None and _mono() - failed_at < _LAMP_FAIL_TTL_S:
            raise RuntimeError(f"lamp {ip} is niet bereikbaar (zojuist mislukt)")
        return _connect_lamp_locked(ip)
    finally:
        lock.release()


def drop_lamp(ip: str):
    _lamp_conns.pop(ip, None)


def reconnect_lamp(ip: str):
    """Gooi een (bv. sessie-verlopen) verbinding weg en bouw 'm opnieuw op met
    dezelfde opgeslagen credentials (geen nieuwe gegevens nodig). Gebruikt
    dezelfde per-IP lock als :func:`lamp`, zodat twee threads die tegelijk
    een sessie-timeout tegenkomen niet allebei gelijktijdig gaan
    herauthenticeren."""
    lock = _acquire_lamp_lock(ip)
    try:
        _lamp_conns.pop(ip, None)
        return _connect_lamp_locked(ip)
    finally:
        lock.release()


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


# --------------------------------------------------------------------------- #
# Google Agenda (OAuth) -- zelfde patroon als spotify_oauth() hierboven: de
# gebruiker opent de auth-url zelf (Pi is headless, kan geen browser openen),
# plakt de volledige redirect-URL terug, en het resulterende token wordt
# persistent opgeslagen (scheduler.agenda.GOOGLE_TOKEN_FILE) zodat een
# herstart niet opnieuw hoeft in te loggen.
#
# De auth-url-aanvraag en de token-uitwisseling zijn twee losse HTTP-
# requests (en dus twee losse Flow-objecten) -- zowel de CSRF-state ALS de
# PKCE code_verifier die Google's Flow.authorization_url() genereert (zie
# google_calendar_oauth() hieronder) moeten daarom expliciet tussen die twee
# bewaard en aan het tweede Flow-object meegegeven worden. Zonder de
# code_verifier stuurt fetch_token() 'm als None mee en weigert Google de
# uitwisseling met "invalid_grant: Missing code verifier"; zonder de state
# wordt de CSRF-check stilzwijgend overgeslagen.
#
# state en code_verifier horen bij precies dezelfde inlogpoging, dus samen
# als één paar bewaard/uitgelezen (nooit de state van de ene poging met de
# verifier van een andere combineren). _google_oauth_pending is bewust een
# simpele, met een lock beschermde losse waarde (net als _lamp_conns
# hierboven) -- dit dashboard heeft maar één operator tegelijk; start een
# tweede "Verbind met Google"-klik een nieuwe poging, dan overschrijft die
# het paar van een nog lopende poging, waarna die oude poging bij het
# inwisselen alsnog netjes afgewezen wordt (state komt niet meer overeen) --
# nooit een mismatch tussen state en verifier van twee verschillende
# pogingen.
# --------------------------------------------------------------------------- #
_google_oauth_pending: dict = {"state": None, "code_verifier": None}
_google_oauth_lock = threading.Lock()


def set_google_oauth_pending(state: str, code_verifier: str) -> None:
    with _google_oauth_lock:
        _google_oauth_pending["state"] = state
        _google_oauth_pending["code_verifier"] = code_verifier


def pop_google_oauth_pending():
    """Eenmalig uitleesbaar (CSRF-state + PKCE-verifier, geen replay) --
    leest en wist allebei in één keer, zodat een mislukte of verlopen
    poging nooit hergebruikt kan worden bij een volgende login."""
    with _google_oauth_lock:
        state = _google_oauth_pending["state"]
        code_verifier = _google_oauth_pending["code_verifier"]
        _google_oauth_pending["state"] = None
        _google_oauth_pending["code_verifier"] = None
        return state, code_verifier


def _is_loopback_redirect(uri: str) -> bool:
    from urllib.parse import urlparse

    return (urlparse(uri).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")


def google_calendar_oauth(state: str | None = None, code_verifier: str | None = None):
    import os

    from google_auth_oauthlib.flow import Flow

    from scheduler.agenda import GOOGLE_SCOPES

    cid = config.secret("GOOGLE_CLIENT_ID")
    csecret = config.secret("GOOGLE_CLIENT_SECRET")
    if not (cid and csecret):
        return None
    redirect_uri = config.secret("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/callback")
    if redirect_uri.startswith("http://") and _is_loopback_redirect(redirect_uri):
        # RFC 8252: een loopback-redirect-URI (127.0.0.1/localhost) mag plain
        # http zijn -- die verlaat het apparaat nooit. oauthlib weigert elke
        # http-redirect standaard; dit staat 'm alleen voor dit specifieke,
        # onschadelijke geval expliciet toe (geen algehele verzwakking: een
        # niet-loopback http-redirect blijft gewoon geweigerd).
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    client_config = {
        "web": {
            "client_id": cid,
            "client_secret": csecret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri,
        state=state, code_verifier=code_verifier,
        # Geen code_verifier meegegeven (1e request, auth-url) -> laat PKCE 'm
        # zelf genereren, zoals altijd. Wél meegegeven (2e request, token-
        # uitwisseling) -> nooit stilzwijgend een NIEUWE laten genereren, die
        # zou toch niet meer matchen met de code_challenge die Google al kreeg.
        autogenerate_code_verifier=(code_verifier is None),
    )


def active_device_id(sp):
    """ID van het apparaat om playback naar te sturen.

    1. Het apparaat waar NU al actief op gespeeld wordt -- nooit een lopende
       sessie op je telefoon/laptop/Sonos ongevraagd overnemen.
    2. Anders (niks actief) de Pi's eigen Spotify Connect-apparaat
       (spotify.pi_device_name), als DIE beschikbaar is -- Kamer-AI is een
       kamerassistent met een eigen luidspreker, dus start je iets vanuit
       het dashboard/de assistent zonder zelf een apparaat te kiezen, dan
       is de eigen Pi-speaker de logische standaard.
    3. Anders None -- GEEN ander willekeurig apparaat raden (was voorheen
       "het eerste apparaat in de lijst", een volgorde die Spotify niet
       garandeert en net zo goed je telefoon of laptop kon zijn). Spotify
       geeft dan zelf een nette NO_ACTIVE_DEVICE-fout (afgevangen door
       media_api.py::_play_error), i.p.v. dat hier geraden wordt."""
    try:
        pb = sp.current_playback()
        if pb and pb.get("device", {}).get("id"):
            return pb["device"]["id"]
    except Exception as exc:  # noqa: BLE001
        _degrade("active_device_id/current_playback", exc)
    try:
        pi_name = config.get("spotify.pi_device_name", "Kamer-AI")
        for d in sp.devices().get("devices", []):
            # Op naam EN type (librespot/raspotify meldt zich bij Spotify als
            # "Speaker") -- een ander apparaat dat toevallig dezelfde naam
            # draagt (bv. hernoemd door de gebruiker) matcht dan niet alsnog.
            if d.get("id") and d.get("name") == pi_name and d.get("type") == "Speaker":
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


def _discover_pi_spotify_device() -> dict | None:
    """Eenmalige, korte mDNS-opzoeking naar de Pi's EIGEN Spotify Connect-
    apparaat (spotify.pi_device_name), via dezelfde _spotify-connect._tcp
    servicetype die raspotify/librespot al gebruikt.

    Waarom dit nodig is: Spotify's Web API (sp.devices(), zie
    active_device_id() hierboven) geeft een zeroconf-apparaat pas terug
    NADAT iemand het één keer via de officiële Spotify-app heeft
    geselecteerd en er iets op afgespeeld heeft -- daarvóór is het al wel
    gewoon op het netwerk te vinden (bevestigd: live avahi-browse liet het
    zien als "Kamer-AI" op _spotify-connect._tcp, ook toen sp.devices()
    'm nog niet teruggaf). Zonder deze losse mDNS-check leek de Pi vanuit
    het dashboard niet te bestaan, terwijl-ie allang op het netwerk draaide.

    Nooit een hardcoded poort/adres: librespot kiest bij elke herstart een
    nieuwe willekeurige zeroconf-poort, dus altijd vers via mDNS opzoeken."""
    try:
        from zeroconf import Zeroconf
    except ImportError:  # niet geïnstalleerd -> deze functie is puur optioneel
        return None
    pi_name = config.get("spotify.pi_device_name", "Kamer-AI")
    zc = Zeroconf()
    try:
        info = zc.get_service_info(
            "_spotify-connect._tcp.local.",
            f"{pi_name}._spotify-connect._tcp.local.",
            timeout=2000,
        )
    except Exception as exc:  # noqa: BLE001 - een mDNS-hikje mag /api/devices nooit slopen
        _degrade("discover_pi_spotify_device", exc)
        return None
    finally:
        zc.close()
    if not info:
        return None
    addrs = info.parsed_addresses()
    return {"name": pi_name, "host": (addrs[0] if addrs else info.server), "port": info.port}


def pi_spotify_device(fresh: bool = False) -> dict | None:
    """Gecached (TTL) resultaat van _discover_pi_spotify_device() -- een
    mDNS-opzoeking kost ~1-2s, en /api/devices wordt elke paar seconden
    gepolld door media.js; zonder cache zou dat een trage, blokkerende
    lookup op elke poll betekenen."""
    if fresh:
        _data_cache.pop("spotify:pi_device", None)
    return _cached("spotify:pi_device", 60, _discover_pi_spotify_device, fallback=None)


def start_playback(sp, **kwargs):
    """Speel af op het beste apparaat: het actieve, anders de Pi als bewuste
    standaard (active_device_id()), nooit een willekeurig ander apparaat.
    Gedeeld door Dashboard/backend/media_api.py (dashboard-knoppen) EN
    sound_system/muziek.py::speel_muziek() (spraak/AI-commando's als "speel
    muziek af") zodat playback zonder expliciet gekozen apparaat overal
    consistent naar de Pi valt, i.p.v. aan Spotify's eigen ondoorzichtige
    standaardkeuze over te laten (dat kon voorheen de telefoon/laptop van
    de gebruiker zijn i.p.v. de Pi, afhankelijk van wat Spotify zelf koos)."""
    dev = active_device_id(sp)
    if dev:
        wake_device(sp, dev)
        kwargs["device_id"] = dev
    sp.start_playback(**kwargs)


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
    """Gecached (TTL, single-flight): heeft de gebruiker OVERHAUPT een Spotify-
    apparaat geregistreerd (voor de 'geen apparaat'-waarschuwing op het
    dashboard) -- los van of er nu iets actief speelt en los van of het
    specifiek de Pi is. active_device_id() is hier bewust NIET voor bedoeld
    sinds die alleen nog het actieve apparaat of de Pi teruggeeft (zie
    aldaar); deze check kijkt naar de volledige apparatenlijst."""
    def has():
        s = _services.get("spotify")
        try:
            return bool(s and s.sp.devices().get("devices"))
        except Exception:  # noqa: BLE001 - check zelf mag niet zeuren
            return True

    return _cached("spotify:has_device", _HEALTH_TTL, has, fallback=True)


def service_status() -> dict:
    names = ("spotify", "radio", "weer", "agenda")

    def stale_names():
        now = time.time()
        return [n for n in names if not (_health_cache.get(n) and _health_cache[n][2] >= now)]

    if stale_names():
        # Eén thread ververst de probes (parallel -- scheelt seconden op trage
        # wifi); anderen serveren het oude resultaat i.p.v. elk zelf dezelfde
        # (mogelijk hangende) probes te draaien en workers vast te zetten.
        first_time = any(n not in _health_cache for n in names)
        if _status_lock.acquire(timeout=_CACHE_FIRST_WAIT_S if first_time else 0):
            try:
                stale = stale_names()
                if stale:
                    from concurrent.futures import ThreadPoolExecutor

                    with ThreadPoolExecutor(max_workers=len(stale)) as ex:
                        for n, res in zip(stale, ex.map(_probe, stale)):
                            _health_cache[n] = (res[0], res[1], time.time() + _HEALTH_TTL)
            finally:
                _status_lock.release()

    out = {}
    for n in names:
        entry = _health_cache.get(n)
        ok, err = (entry[0], entry[1]) if entry else (False, "wordt gecontroleerd")
        out[n] = {"ok": ok, "error": err}
        if n == "spotify":
            relink = bool(err and "opnieuw koppelen" in err)
            out[n]["needs_relink"] = relink
            if ok and not relink and not _spotify_has_device():
                out[n]["no_device"] = True
                out[n]["error"] = "geen apparaat actief"
    return out


# /api/overview wordt elke ~10s gepolld; weer/agenda hoeven niet zo vaak vers
_CACHE_FIRST_WAIT_S = 4.0    # zo lang mag een aanroep wachten op de EERSTE ophaalactie van een key
_status_lock = threading.Lock()


def _expire(key: str) -> None:
    """Markeer een cache-entry als verlopen maar BEWAAR de waarde: de eerstvolgende
    aanroep ververst (single-flight), gelijktijdige aanroepen krijgen ondertussen
    de oude waarde i.p.v. te blokkeren. (Wegpoppen, zoals eerder bij fresh=True/
    invalidate, maakte van elke follower een wachtende thread.)"""
    hit = _data_cache.get(key)
    if hit:
        _data_cache[key] = (hit[0], 0.0)


def _store(key: str, ttl: float, val):
    # een foutresultaat kort cachen zodat we niet elke 10s opnieuw hameren
    _data_cache[key] = (val, time.time() + (20 if isinstance(val, dict) and val.get("error") else ttl))


def _cached(key: str, ttl: float, produce, fallback=None):
    """Single-flight + stale-while-revalidate.

    Precies één thread ververst een verlopen key (``produce()`` mag lang
    duren); gelijktijdige aanroepen krijgen meteen de laatste bekende waarde
    terug i.p.v. een waitress-worker vast te zetten achter de lock. Lokaal
    gereproduceerd (tools/stress_dashboard.py): met een trage Spotify/weer/
    agenda stapelden de polls van een paar tabbladen zich op tot alle 16
    workers bezet waren en het HELE dashboard bevroor. Bestond er nog
    helemaal niets voor deze key, dan wacht een aanroep (begrensd) op de
    lopende eerste ophaalactie en valt daarna terug op ``fallback``. Zelfde
    principe verhinderde eerder al de cache-stampede (12 gelijktijdige
    /api/overview-aanvragen = 6+ parallelle Google Calendar-verbindingen)."""
    hit = _data_cache.get(key)
    if hit and hit[1] >= time.time():
        return hit[0]
    lock = _named_lock(_data_cache_locks, key)
    if lock.acquire(blocking=False):
        try:
            hit = _data_cache.get(key)
            if hit and hit[1] >= time.time():   # net door een ander gevuld
                return hit[0]
            val = produce()
            _store(key, ttl, val)
            return val
        finally:
            lock.release()
    # een andere thread ververst al
    if hit:
        return hit[0]                   # oud maar bruikbaar: geen thread vastzetten
    if lock.acquire(timeout=_CACHE_FIRST_WAIT_S):
        try:
            hit = _data_cache.get(key)
            if hit:
                return hit[0]
            val = produce()             # de eerste ophaalactie mislukte (exception): zelf proberen
            _store(key, ttl, val)
            return val
        finally:
            lock.release()
    return fallback


def weather_data(city=None, fresh: bool = False) -> dict:
    key = f"weather:{city or '_'}"
    if fresh:
        _expire(key)

    def fetch():
        w = svc("weer")
        if not w:
            return {"error": _errors.get("weer", "weer niet beschikbaar")}
        return w.fetch_weather(city) or {"error": "geen weerdata"}

    return _cached(key, 600, fetch, fallback={"error": "weer wordt opgehaald"})   # 10 min


def calendar_today(fresh: bool = False) -> dict:
    if fresh:
        _expire("calendar:today")

    def fetch():
        cal = svc("agenda")
        if not cal:
            return {"events": [], "error": _errors.get("agenda")}
        try:
            return {"events": cal.return_todays_events()}
        except Exception as exc:  # noqa: BLE001
            return {"events": [], "error": str(exc)}

    return _cached("calendar:today", 300, fetch,
                   fallback={"events": [], "error": "agenda wordt opgehaald"})   # 5 min


def calendar_events(start, end) -> dict:
    """Voor de Kalender-tab (/api/calendar/events): platte,
    provider-onafhankelijke events voor een willekeurig datumbereik. Zonder
    caching deed elke navigatie-klik in calendar.js (vorige/volgende/
    vandaag/weergave wisselen) een verse, live aanroep naar Google/iCloud --
    calendar.js heeft geen eigen debounce/inflight-guard, dus snel
    doorbladeren kon veel onnodige aanroepen achter elkaar triggeren tegen
    een afhankelijkheid die al eerder deze sessie echte SSL-/timeoutfouten
    gaf. Zelfde caching als calendar_today(), met het bereik in de
    cache-key zodat elk bereik zijn eigen cache heeft."""
    key = f"calendar:events:{start}:{end}"

    def fetch():
        cal = svc("agenda")
        if not cal:
            return {"events": [], "error": _errors.get("agenda", "agenda niet beschikbaar")}
        try:
            events = cal.get_normalized_events(start, end)
        except Exception as exc:  # noqa: BLE001 - een onverwachte fout mag de tab niet slopen
            return {"events": [], "error": str(exc)}
        # Geen accounts gekoppeld gaf een lege kalender zonder enige uitleg
        err = cal.error or (None if getattr(cal, "calendars", None) else
                            "Geen agenda gekoppeld -- stel een account in via Settings.")
        return {"events": events, "error": err}

    return _cached(key, 300, fetch,
                   fallback={"events": [], "error": "agenda wordt opgehaald"})   # 5 min


def invalidate(*keys: str) -> None:
    """Gooi cache-keys weg na een actie die de getoonde staat verandert (bv.
    pauze/volgende/apparaat kiezen), zodat de eerstvolgende refresh geen
    (max. paar seconden) oude waarde toont."""
    for k in keys:
        _expire(k)


def current_playing() -> dict:
    """Gecached (2s, single-flight): met N tabbladen die elke ~4s pollen was dit
    N Spotify-API-aanroepen per poll -- en bij een trage Spotify N vastgezette
    workers. Acties in media_api roepen invalidate("now_playing") aan."""
    return _cached("now_playing", 2.0, _current_playing_uncached, fallback={"type": "none"})


def _current_playing_uncached() -> dict:
    sp = svc("spotify")
    if sp:
        try:
            data = sp.current_track()
            if data.get("type") == "spotify":
                data.setdefault("volume", 50)   # current_track levert 'm al mee
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


def spotify_device_list() -> dict:
    """Voor /api/devices: Spotify's Web API-apparaten aangevuld met de Pi zelf
    (mDNS) zolang die nog niet gekoppeld is. Gecached (5s, single-flight) --
    media.js pollt dit, en bij een trage Spotify stapelden die polls op."""
    return _cached("spotify:devices", 5.0, _build_spotify_device_list,
                   fallback={"success": False, "error": "apparaten worden opgehaald"})


def _build_spotify_device_list() -> dict:
    try:
        sp = sp_dj().sp
    except Exception as exc:  # noqa: BLE001 - geen Spotify-koppeling
        return {"success": False, "error": str(exc)}

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

    # De Pi is via Spotify's Web API pas zichtbaar NADAT iemand 'm één keer
    # via de officiële Spotify-app heeft geselecteerd en er iets op heeft
    # afgespeeld (zie active_device_id()). Daarvóór is-ie al wel gewoon op het
    # netwerk te vinden via mDNS -- reken dat mee bij het bepalen of er "niks"
    # is, anders krijgt precies het geval waar deze check voor bedoeld is
    # (Web API geeft niks terug) de Pi nooit de kans om zich alsnog te melden.
    pi_name = config.get("spotify.pi_device_name", "Kamer-AI")
    pi_known = any(d.get("name") == pi_name for d in devs.values()) or bool(playing and playing["name"] == pi_name)
    local_pi = None if pi_known else pi_spotify_device()

    if not devs and not playing and not local_pi and errors:
        return {"success": False, "error": errors[0]}

    out = [{"id": d["id"], "name": d.get("name", "?"), "type": d.get("type", "?"),
            "active": bool(d.get("is_active"))} for d in devs.values()]
    if playing:
        out.append(playing)
    if local_pi:
        # Vooraan: het is Kamer-AI's eigen luidspreker, zodat je meteen ziet
        # dat-ie er is en (via de tooltip) wat je moet doen om 'm te koppelen.
        out.insert(0, {"id": None, "name": pi_name, "type": "Speaker",
                       "active": False, "local_only": True})
    return {"success": True, "warning": errors[0] if errors else None, "devices": out}
