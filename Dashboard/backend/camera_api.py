"""Camera: one capture thread feeds every viewer the latest JPEG, so CPU stays
flat regardless of how many tabs are open, and capture stops when nobody watches.
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, Response, jsonify

import config
from Dashboard.backend.auth import require_password

camera_bp = Blueprint("camera", __name__)

_mono = time.monotonic   # seam voor tests (backoff/staleness zonder echt wachten)

# Herstart-backoff na opeenvolgende mislukte opens/reads (USB-camera eruit,
# libcamera weg): 1e mislukking -> direct opnieuw (USB-hikje), daarna 5s,
# 10s, 20s, 40s, max 60s. Zonder dit startte presence (elke ~3s
# keep_alive -> _ensure_running) elke tick opnieuw een open-poging met een
# V4L2-waarschuwing in het journaal per poging.
_BACKOFF_BASE_S = 5.0
_BACKOFF_MAX_S = 60.0
# Als alleen de aanwezigheidsdetectie meekijkt: zoveel s tussen frames
# (presence.interval_s/2, begrensd) i.p.v. de volle camera.fps.
_PRESENCE_ONLY_MIN_S = 0.5
_PRESENCE_ONLY_MAX_S = 2.0


_DRAIN_GRABS = 3


def _cv2_reader(cap):
    """``read(fresh=False) -> (ok, frame)`` voor een cv2.VideoCapture.

    V4L2 houdt een rij van ~4 buffers vast. Bij een lage leesfrequentie (presence-only:
    1 frame per ~0,5-2 s) is ``cap.read()`` het OUDSTE frame uit die rij: tot ~4
    leesintervallen (secondes) oud, dus een persoon werd pas seconden later "gezien".
    Met ``fresh=True`` trekken een paar goedkope ``grab()``'s (geen decode) de rij leeg,
    zodat het teruggegeven frame het nieuwste is. Zonder ``grab`` (of bij een mislukte
    grab) valt het terug op een gewone ``read()``."""
    grab = getattr(cap, "grab", None)

    def read(fresh: bool = False):
        if fresh and grab is not None:
            for _ in range(_DRAIN_GRABS):
                try:
                    if not grab():
                        break
                except Exception:  # noqa: BLE001 - een falende grab mag de gewone read niet verhinderen
                    break
        return cap.read()

    return read


class _Camera:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._latest = None
        self._seq = 0
        self._viewers = 0
        self._stop = threading.Event()
        self._last_grab = 0.0        # laatste losse snapshot-aanvraag
        self._keep_alive = False     # bv. aanwezigheidsdetectie: blijf draaien zonder kijkers
        self.error = None
        self._wake = threading.Event()   # maakt de capture-loop meteen wakker (nieuwe kijker/stop)
        self._consec_fail = 0            # opeenvolgende mislukte open/read-pogingen
        self._retry_at = 0.0             # _mono()-tijdstip waarvoor een herstart wordt uitgesteld

    def keep_alive(self, on: bool) -> None:
        """Voorkom dat de capture-thread stopt als er geen MJPEG-kijkers of
        snapshot-aanvragen zijn (gebruikt door de aanwezigheidsdetectie, die
        continu frames nodig heeft ongeacht of er een browser openstaat)."""
        self._keep_alive = bool(on)
        if on:
            self._ensure_running()

    def _max_viewers(self) -> int:
        # elke kijker houdt een waitress-worker bezig; laat er genoeg vrij
        return max(1, int(config.get("camera.max_viewers", 3)))

    def acquire(self):
        """Reserveer één kijkplek (atomair). Geeft een idempotente release-
        functie terug, of ``None`` als het vol zit."""
        with self._lock:
            if self._viewers >= self._max_viewers():
                return None
            self._viewers += 1
        self._wake.set()   # een echte kijker -> meteen de volle framerate
        released = [False]

        def release():
            with self._lock:
                if released[0]:
                    return
                released[0] = True
                self._viewers = max(0, self._viewers - 1)
                if self._viewers == 0:
                    self._stop.set()
                    self._wake.set()

        return release

    def _open_source(self, w, h, fps):
        """(read_fn, close_fn) voor de beste beschikbare camera-backend.

        read_fn -> (ok, frame_bgr). Gooit een uitzondering als de backend niet
        kan openen; de caller probeert dan de volgende.
        """
        backend = config.get("camera.backend", "auto")
        errs = []

        if backend in ("auto", "opencv"):
            try:
                import cv2

                idx = config.get("camera.device_index", 0)
                cap = cv2.VideoCapture(idx)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                cap.set(cv2.CAP_PROP_FPS, fps)
                if not cap.isOpened():
                    cap.release()
                    raise RuntimeError(f"opencv: camera {idx} gaat niet open")
                return _cv2_reader(cap), cap.release
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc))

        if backend in ("auto", "picamera2"):
            try:
                from picamera2 import Picamera2

                pc = Picamera2()
                # 'RGB888' levert bij picamera2 juist BGR-geheugenvolgorde op,
                # precies wat cv2.imencode verwacht — niet omdraaien.
                pc.configure(pc.create_video_configuration(
                    main={"size": (w, h), "format": "RGB888"}))
                pc.start()
                return (lambda fresh=False: (True, pc.capture_array())), (lambda: (pc.stop(), pc.close()))
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc))

        raise RuntimeError("; ".join(errs) or f"onbekende camera.backend {backend!r}")

    def _note_failure(self) -> None:
        """Registreer een mislukte open/read en plan de vroegst mogelijke
        herstart (backoff, zie _BACKOFF_*)."""
        self._consec_fail += 1
        n = self._consec_fail
        delay = 0.0 if n <= 1 else min(_BACKOFF_MAX_S, _BACKOFF_BASE_S * 2 ** (n - 2))
        self._retry_at = _mono() + delay

    def _presence_only(self) -> bool:
        """True als geen mens/browser meekijkt en alleen de aanwezigheids-
        detectie (die zelf precies één kijkerplek vasthoudt) frames nodig heeft
        -- dan is de volle camera.fps (resize + JPEG-encode per frame) puur
        verspilde CPU op een Pi: presence gebruikt er maar één per ~3s."""
        return (
            self._keep_alive
            and self._viewers <= 1
            and time.time() - self._last_grab > 30
        )

    def _frame_delay(self) -> float:
        if self._presence_only():
            interval = float(config.get("presence.interval_s", 3.0) or 3.0)
            return min(_PRESENCE_ONLY_MAX_S, max(_PRESENCE_ONLY_MIN_S, interval / 2))
        return 1.0 / max(1, config.get("camera.fps", 10))

    def _run(self):
        try:
            import cv2
        except Exception as exc:  # noqa: BLE001
            self.error = f"opencv ontbreekt: {exc}"
            return
        w, h = config.get("camera.width", 640), config.get("camera.height", 360)
        try:
            read, close = self._open_source(w, h, config.get("camera.fps", 10))
        except Exception as exc:  # noqa: BLE001
            self.error = f"camera kan niet worden geopend ({exc})"
            self._note_failure()
            return
        self.error = None
        got_frame = False
        try:
            while not self._stop.is_set():
                # niemand kijkt (geen MJPEG-viewer), geen snapshot in 30s, en niemand
                # vraagt om 'm actief te houden (aanwezigheidsdetectie) -> stop
                if not self._keep_alive and self._viewers == 0 and time.time() - self._last_grab > 30:
                    break
                q = int(config.get("camera.jpeg_quality", 55))
                ok, frame = read(fresh=self._presence_only())
                if not ok or frame is None:
                    self.error = "geen beeld van camera"
                    self._note_failure()
                    break
                if not got_frame:   # een echt frame: de bron is gezond, backoff terug op nul
                    got_frame = True
                    self._consec_fail = 0
                frame = cv2.resize(frame, (w, h))
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), q])
                if ok:
                    with self._lock:
                        self._seq += 1
                        self._latest = (buf.tobytes(), self._seq, _mono())
                self._wake.wait(self._frame_delay())
                self._wake.clear()
        finally:
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                print(f"[camera] kon bron niet sluiten: {exc!r}")
            with self._lock:
                self._latest = None

    def _ensure_running(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                if _mono() < self._retry_at:
                    return   # backoff na herhaalde mislukkingen, zie _note_failure()
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
                self._thread.start()

    def latest_jpeg(self, max_age_s: float | None = None) -> bytes | None:
        """Het nieuwste frame als JPEG-bytes, of ``None`` als er nog niks is.

        Met ``max_age_s`` ook ``None`` als dat frame ouder is: een vastgelopen
        camera-lees (USB-brownout, device dat blijft hangen) laat anders het
        LAATSTE frame eeuwig 'vers' lijken -- een bevroren beeld met een persoon
        erin zou de kamer voor altijd bezet houden (lamp gaat nooit uit)."""
        with self._lock:
            if not self._latest:
                return None
            if max_age_s is not None and _mono() - self._latest[2] > max_age_s:
                return None
            return self._latest[0]

    def frame_age_s(self) -> float | None:
        with self._lock:
            return None if not self._latest else round(_mono() - self._latest[2], 2)

    def release(self):
        self._stop.set()
        self._wake.set()

    def frames(self, on_exit=lambda: None):
        """Aanroeper doet eerst acquire(); ``on_exit`` is de release-functie
        daarvan (ook geregistreerd via response.call_on_close voor het geval
        de stream nooit start)."""
        self._ensure_running()
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
                    if idle > 50 and self.error:
                        break
                time.sleep(1.0 / max(1, config.get("camera.fps", 10)))
        finally:
            on_exit()


camera = _Camera()


@camera_bp.route("/video_feed")
@require_password
def video_feed():
    if not config.get("camera.enabled", True):
        return Response("camera uit", status=503)
    release = camera.acquire()
    if release is None:
        return Response("Te veel camerakijkers open — sluit een ander tabblad.", status=503)
    resp = Response(
        camera.frames(release),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )
    resp.call_on_close(release)   # vangt ook 'stream nooit gestart' af
    return resp


@camera_bp.route("/api/camera_snapshot")
@require_password
def camera_snapshot():
    """Losse JPEG van het laatste frame — de browser-detectie draait hierop
    (een <img> met een MJPEG-stream levert geen leesbare pixels)."""
    if not config.get("camera.enabled", True):
        return Response("camera uit", status=503)
    camera._last_grab = time.time()
    camera._wake.set()
    camera._ensure_running()
    latest = camera._latest
    if not latest:
        return Response("nog geen beeld", status=503)
    return Response(latest[0], mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@camera_bp.route("/api/camera_status")
def camera_status():
    return jsonify({
        "enabled": bool(config.get("camera.enabled", True)),
        "error": camera.error,
        "viewers": camera._viewers,
        "frame_age_s": camera.frame_age_s(),
    })
