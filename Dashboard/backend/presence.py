"""Aanwezigheidsdetectie: hoeveel mensen zijn er in de kamer, en (optioneel)
automatisch de lamp aan/uit op basis daarvan.

Draait als één achtergrond-thread die continu (met een instelbare, lage
frequentie) het nieuwste cameraframe van :mod:`Dashboard.backend.camera_api`
hergebruikt — geen tweede camera-verbinding, geen extra CPU-last dan wat de
camera toch al doet. Er wordt nooit een beeld opgeslagen: elk frame wordt in
het geheugen bekeken en meteen weggegooid.

Ontwerp (bewust eenvoudig, zie de opdracht: "geen ingewikkeld systeem als een
eenvoudige betrouwbare state voldoende is"):

* Hysteresis (``presence.consecutive_required`` opeenvolgende positieve
  detecties) voordat EMPTY -> OCCUPIED omslaat, en een grace period
  (``presence.empty_grace_s``) voordat OCCUPIED -> EMPTY omslaat. Zo flipt
  een enkel gemist frame de kamerstatus niet meteen.
* De automatische lamp wordt alleen aangeraakt OP een kamerstatus-overgang,
  niet op elke poll-tick. Daardoor overschrijft de auto-logica een
  handmatige actie nooit meteen: zet jij de lamp handmatig uit terwijl de
  kamer OCCUPIED blijft, dan gebeurt er niets totdat de kamer weer leeg en
  opnieuw bezet raakt (een echte nieuwe aanwezigheids-gebeurtenis).
* De 21:30-regel (``presence.auto_light_block_after``) blokkeert uitsluitend
  automatisch AAN-zetten; automatisch UIT mag altijd. De vergelijking gebeurt
  op de kloktijd (``datetime.time``), dus dat blijft correct rond middernacht
  — geen opgeslagen/verouderde datum, geen stringvergelijking.
"""
from __future__ import annotations

import asyncio
import datetime
import threading
import time

from flask import Blueprint, jsonify

import config
from Dashboard.backend import services as S
from logic.logger import log

_DEFAULT_BLOCK_AFTER = "21:30"
# Zoveel keer een mislukte automatische lampactie herproberen (op de eerstvolgende
# presence-ticks, dus met de bestaande interval_s -- geen extra loop) voordat we
# opgeven tot de volgende EMPTY<->OCCUPIED-overgang.
_MAX_LIGHT_RETRIES = 3


def _is_session_timeout(exc: BaseException) -> bool:
    """De tapo-library (Rust/PyO3, geen eigen Python-exceptieklasse) geeft
    zo'n fout als tekst terug, bv.:
    Tapo(Unauthorized { kind: "SESSION_TIMEOUT", description: "..." })
    Vandaar een tekst-check i.p.v. een except-type."""
    msg = str(exc)
    return "SESSION_TIMEOUT" in msg or "Unauthorized" in msg


def _parse_hhmm(s: str, default: str = _DEFAULT_BLOCK_AFTER) -> datetime.time | None:
    """'21:30' -> time(21, 30). Lege string/onleesbare waarde -> None (geen blokkade)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        hh, mm = s.split(":", 1)
        return datetime.time(int(hh), int(mm))
    except (ValueError, TypeError):
        if s != default:
            log("LIGHT", f"Ongeldige auto_light_block_after {s!r}, val terug op {default}")
            return _parse_hhmm(default, default)
        return None


def is_auto_light_blocked(now: datetime.datetime | None = None) -> bool:
    """De harde 21:30-regel. Puur en side-effect-vrij zodat 'ie makkelijk te
    testen is voor/na 21:30 en rond middernacht."""
    cutoff = _parse_hhmm(config.get("presence.auto_light_block_after", _DEFAULT_BLOCK_AFTER))
    if cutoff is None:
        return False
    return (now or datetime.datetime.now()).time() >= cutoff


class PresenceWorker:
    """Eén gedeelde achtergrond-thread. Start() is idempotent (veilig meerdere
    keren aan te roepen, bv. bij een test-herlaad)."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self.room_state = "EMPTY"
        self.last_count = 0
        self.last_detect_ms: float | None = None   # duur van de laatste HOG-detectie (performance-meting)
        self.last_change: float | None = None   # time.time() van de laatste EMPTY<->OCCUPIED-overgang
        self._positive_streak = 0
        self._last_positive_at: float | None = None
        self._camera_hold = None   # release-functie van camera.acquire(), of None
        self._camera_full_warned = False

        # Sensor (camera) tijdelijk niet beschikbaar -> "ONBEKEND", niet stiekem
        # "geen mensen". Zie _tick(): telt niet mee voor de EMPTY-kant van de
        # hysteresis (een camerahapering mag nooit lijken op "persoon weg").
        self._sensor_unknown = False
        self._sensor_unknown_warned = False

        # AUTO/MANUAL, puur voor zichtbaarheid (/api/presence): de bestaande
        # edge-triggered actuatie (lamp alleen aanraken OP een overgang, zie
        # _transition) zorgde al dat een handmatige actie nooit meteen
        # overschreven wordt -- deze vlag maakt dat gedrag expliciet i.p.v.
        # impliciet. Terug naar AUTO gebeurt vanzelf bij de eerstvolgende
        # echte aanwezigheids-overgang (geen arbitraire timeout nodig: de
        # volgende keer dat de kamer leeg/bezet raakt is het natuurlijke,
        # hardware-gegronde moment waarop automatisering weer vers mag
        # beslissen).
        self._mode = "AUTO"
        # Telt handmatige lampacties. Een automatische actie die al besloten was
        # maar nog op de (trage) Tapo-verbinding wacht, controleert dit vlak voor
        # het commando: is er intussen handmatig ingegrepen, dan slaat 'ie zijn
        # commando over -- handmatig wint altijd van een eerdere auto-beslissing.
        self._manual_epoch = 0

        # Retry-status voor een mislukte automatische lampactie (zie _retry_light).
        # None = geen mislukte poging openstaand -> retry-tick doet dan niets.
        self._pending_light: bool | None = None
        self._light_retries = 0

        # Herhaalde identieke onverwachte fout niet elke tick (3s) opnieuw
        # loggen: ~29k regels/dag op een Pi zonder logrotatie.
        self._last_loop_error: str | None = None
        self._last_loop_error_at = 0.0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="presence", daemon=True)
            self._thread.start()

    def _run_once(self) -> float:
        """Eén ronde van de worker; geeft de wachttijd tot de volgende terug.
        Vangt alles af: deze thread mag nooit doodgaan."""
        interval = 5.0
        try:
            if config.get("presence.enabled", False):
                interval = max(0.5, float(config.get("presence.interval_s", 3.0)))
                self._tick()
            else:
                self._release_camera()
                self.room_state = "EMPTY"
                self._positive_streak = 0
                self._pending_light = None
                self._light_retries = 0
                self._sensor_unknown = False
                self._mode = "AUTO"
            self._last_loop_error = None
        except Exception as exc:  # noqa: BLE001 - deze thread mag nooit doodgaan
            msg = f"{type(exc).__name__}: {exc}"
            now = time.time()
            if msg != self._last_loop_error or now - self._last_loop_error_at > 600:
                log("PEOPLE", f"Onverwachte fout in aanwezigheids-worker: {msg}")
                self._last_loop_error = msg
                self._last_loop_error_at = now
        return interval

    def _loop(self):
        while not self._stop.is_set():
            self._stop.wait(self._run_once())
        self._release_camera()

    # ------------------------------------------------------------------ #
    # Eén meetronde
    # ------------------------------------------------------------------ #
    def _get_frame(self):
        if not config.get("camera.enabled", True):
            self._release_camera()
            return None
        try:
            from Dashboard.backend.camera_api import camera
        except Exception as exc:  # noqa: BLE001
            log("PEOPLE", f"Camera-module niet beschikbaar: {exc}")
            return None
        if self._camera_hold is None:
            self._camera_hold = camera.acquire()
            if self._camera_hold is None:
                if not self._camera_full_warned:
                    log("PEOPLE", "Camera vol (max_viewers) — probeer later opnieuw")
                    self._camera_full_warned = True
                return None
            self._camera_full_warned = False
        # Elke tick opnieuw (goedkoop, idempotent): een settings-wijziging elders
        # (reset_services()) stopt de capture-thread altijd, ook al houden wij
        # 'm 'levend' — dit herstart 'm dan vanzelf i.p.v. voorgoed op 0 te blijven.
        camera.keep_alive(True)
        # Een frame dat ouder is dan dit telt als "geen beeld" (sensor ONBEKEND):
        # een vastgelopen camera-lees liet anders het laatste frame eeuwig 'vers'
        # lijken -- een bevroren beeld mét persoon hield de kamer voor altijd
        # bezet (lamp ging nooit uit), een bevroren leeg beeld idem andersom.
        interval = max(0.5, float(config.get("presence.interval_s", 3.0)))
        jpeg = camera.latest_jpeg(max_age_s=max(10.0, 4 * interval))
        if not jpeg:
            return None
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(jpeg, dtype="uint8")
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as exc:  # noqa: BLE001
            log("PEOPLE", f"Kon frame niet decoderen: {exc}")
            return None

    def _release_camera(self):
        try:
            from Dashboard.backend.camera_api import camera

            camera.keep_alive(False)
        except Exception:  # noqa: BLE001
            pass
        if self._camera_hold is not None:
            self._camera_hold()
            self._camera_hold = None

    def _tick(self):
        from logic.people_detect import count_people

        frame = self._get_frame()
        if frame is None:
            # Sensor/camera even niet beschikbaar (bv. USB-hikje) -- dit is
            # GEEN bevestigde "0 personen"-meting. Niet laten meetellen voor
            # de EMPTY-kant van de hysteresis (zou een camerahapering laten
            # lijken op "persoon weg") en de opgebouwde OCCUPIED-streak ook
            # niet resetten -- gewoon wachten tot er weer echt beeld is.
            self._sensor_unknown = True
            if not self._sensor_unknown_warned:
                log("PEOPLE", "Sensor unavailable -- presence state held (unknown)")
                self._sensor_unknown_warned = True
            self._retry_light()   # los van detectie, mag gewoon doorgaan
            return
        # camera.detect_threshold stond al in Settings ("hoger = minder valse
        # alarmen"), maar werd nooit doorgegeven -- count_people() draaide
        # hierdoor altijd op de meest permissieve hitThreshold (0.0), de
        # instelling deed dus niks voor de automatisering die de lamp echt
        # aanstuurt (alleen voor de client-side browser-preview, zie
        # system_api.py::public_config()).
        threshold = float(config.get("camera.detect_threshold", 0.5) or 0.0)
        # Optionele verkleining vóór de (zware) HOG-detector: gemeten op een
        # dev-machine kost 640x360 ~190ms/frame en 0.6x (384x216) ~20ms --
        # op een Pi 4B is dat het grootste deel van de presence-CPU. Default
        # 1.0 (ongewijzigd gedrag): de nauwkeurigheid op het echte camerabeeld
        # moet eerst live getoetst worden voordat dit omlaag gaat.
        scale = min(1.0, max(0.25, float(config.get("presence.detect_scale", 1.0) or 1.0)))
        t0 = time.perf_counter()
        count = count_people(frame, scale=scale, hit_threshold=threshold, error_value=None)
        self.last_detect_ms = round((time.perf_counter() - t0) * 1000, 1)
        if count is None:
            # De detector zelf faalde (kapotte cv2-build, geheugentekort, ...). Dat is
            # net als "geen beeld" GEEN bevestigde "0 personen": anders telde elke
            # mislukte detectie mee voor de EMPTY-kant (lamp uit terwijl er iemand
            # zit, en nooit meer vanzelf aan). Status ONBEKEND, toestand vasthouden.
            self._sensor_unknown = True
            if not self._sensor_unknown_warned:
                log("PEOPLE", "Detection failed -- presence state held (unknown)")
                self._sensor_unknown_warned = True
            self._retry_light()
            return
        if self._sensor_unknown:
            log("PEOPLE", "Sensor available again")
            if self.room_state == "OCCUPIED":
                # De storing was geen meting: de grace-periode telt vanaf NU, niet
                # vanaf de laatste positieve detectie van vóór de storing. Anders
                # flipte één enkel negatief frame direct na een lange storing de
                # kamer naar EMPTY (lamp uit) zonder dat er ooit een geldige
                # "leeg"-periode van empty_grace_s is doorlopen.
                self._last_positive_at = time.time()
        self._sensor_unknown = False
        self._sensor_unknown_warned = False
        self.last_count = count
        now = time.time()

        if count > 0:
            self._positive_streak += 1
            self._last_positive_at = now
        else:
            self._positive_streak = 0

        required = max(1, int(config.get("presence.consecutive_required", 2)))
        grace = max(0.0, float(config.get("presence.empty_grace_s", 20.0)))

        if self.room_state == "EMPTY" and self._positive_streak >= required:
            self._transition("OCCUPIED", count)
        elif (
            self.room_state == "OCCUPIED"
            and count == 0
            and self._last_positive_at is not None
            and (now - self._last_positive_at) >= grace
        ):
            self._transition("EMPTY", count)
        else:
            # Geen nieuwe overgang deze tick -> eventueel een eerder mislukte
            # automatische lampactie herproberen (zelfde interval_s, geen extra loop).
            self._retry_light()

    def _transition(self, new_state: str, count: int):
        self.room_state = new_state
        self.last_change = time.time()
        label = "person" if count == 1 else "people"
        log("PEOPLE", f"Detected: {count} {label}")
        log("PEOPLE", f"Room state: {new_state}")
        # Een nieuwe overgang start altijd met een schone retry-lei, en geeft
        # de automatisering weer AUTO-controle (zie de uitleg bij self._mode
        # in __init__): een eventuele handmatige override tijdens de vorige
        # bezette/lege periode is hiermee voorbij.
        self._pending_light = None
        self._light_retries = 0
        self._mode = "AUTO"
        want_on = new_state == "OCCUPIED"
        if not self._auto_light(want_on):
            self._pending_light = want_on

    def note_manual_action(self) -> None:
        """Aangeroepen vanuit devices_api.py wanneer de door presence bediende
        lamp handmatig (via het dashboard/de API) is aangestuurd. Verandert
        niets aan de bestaande actuatie (die vecht toch al nooit meteen terug,
        zie _transition) -- maakt alleen expliciet zichtbaar in /api/presence
        dat dit een bewuste, handmatige actie was."""
        if self._mode != "MANUAL":
            log("PEOPLE", "Manual lamp action detected -- automation paused until next presence change")
        self._mode = "MANUAL"
        self._manual_epoch += 1
        # Een openstaande automatische retry (bv. AAN dat eerder mislukte) mag
        # een handmatige actie niet alsnog overrulen: na een handmatige UIT zou
        # de retry de lamp anders weer AAN zetten.
        self._pending_light = None
        self._light_retries = 0

    # ------------------------------------------------------------------ #
    # Lamp-automatisering
    # ------------------------------------------------------------------ #
    def _auto_light(self, on: bool) -> bool:
        """Probeer de lamp te zetten. Geeft True terug bij succes, of als er
        bewust niets gedaan is (automatiek uit / geen lamp / 21:30-regel --
        dat zijn geen fouten, dus daar hoeft niet op geretried te worden).
        False betekent een echte mislukking (netwerk/Tapo-fout) waarvoor
        _retry_light() het later opnieuw mag proberen."""
        if not config.get("presence.auto_light_enabled", False):
            return True
        if on and is_auto_light_blocked():
            log("LIGHT", "Automatic ON blocked: after 21:30")
            return True
        epoch = self._manual_epoch
        try:
            ip = S.lamp_ip(config.get("presence.lamp", 0))
            if not ip:
                log("LIGHT", "Geen lamp geconfigureerd voor aanwezigheidsautomatisering")
                return True
            try:
                lamp = S.lamp(ip)
                if self._manual_epoch != epoch:   # tijdens het verbinden handmatig ingegrepen
                    log("LIGHT", "Automatic light skipped: manual action took over")
                    return True
                asyncio.run(lamp.aan() if on else lamp.uit())
            except Exception as exc:  # noqa: BLE001
                if not _is_session_timeout(exc):
                    raise
                # Sessie verlopen (niet hetzelfde als de lamp offline/onbereikbaar):
                # bestaande verbinding weggooien, opnieuw inloggen met de bestaande
                # config/credentials, en de actie éénmalig opnieuw proberen. Lukt
                # dat ook niet, dan neemt de gewone retrylogica het hierna over.
                log("LIGHT", f"Tapo session timeout gedetecteerd ({exc}) — opnieuw authenticeren")
                lamp = S.reconnect_lamp(ip)
                if self._manual_epoch != epoch:
                    log("LIGHT", "Automatic light skipped: manual action took over")
                    return True
                asyncio.run(lamp.aan() if on else lamp.uit())
            log("LIGHT", "Automatic light ON" if on else "Automatic light OFF")
            return True
        except Exception as exc:  # noqa: BLE001 - Tapo offline mag de worker niet slopen
            log("LIGHT", f"Automatic light {'ON' if on else 'OFF'} failed: {exc}")
            return False

    def _retry_light(self):
        """Herprobeer een mislukte automatische lampactie, hooguit
        _MAX_LIGHT_RETRIES keer, zolang er geen nieuwe overgang is geweest
        (die reset dit zelf al via _transition). Verandert nooit room_state,
        en stuurt nooit een commando als er geen mislukte poging openstaat --
        raakt dus nooit een handmatige actie."""
        if self._pending_light is None or self._light_retries >= _MAX_LIGHT_RETRIES:
            return
        self._light_retries += 1
        if self._auto_light(self._pending_light):
            self._pending_light = None
            self._light_retries = 0

    # ------------------------------------------------------------------ #
    # Status (voor het dashboard)
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        presence_enabled = bool(config.get("presence.enabled", False))
        auto_light_enabled = bool(config.get("presence.auto_light_enabled", False))
        if self._sensor_unknown:
            state = "unknown"
        elif self.room_state == "OCCUPIED":
            state = "present"
        else:
            state = "absent"
        return {
            "enabled": presence_enabled,
            "room_state": self.room_state,
            "last_count": self.last_count,
            "detect_ms": self.last_detect_ms,
            "auto_light_enabled": auto_light_enabled,
            "auto_light_blocked": is_auto_light_blocked(),
            # Rijkere, provider-onafhankelijke vorm voor de Kalender/Settings-UI:
            "state": state,
            "automation_enabled": presence_enabled and auto_light_enabled,
            "mode": self._mode,
            "last_change": (
                datetime.datetime.fromtimestamp(self.last_change).isoformat()
                if self.last_change else None
            ),
        }


worker = PresenceWorker()

presence_bp = Blueprint("presence", __name__)


@presence_bp.route("/api/presence")
def presence_status():
    return jsonify(worker.status())
