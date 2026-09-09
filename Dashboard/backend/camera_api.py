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


class _Camera:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._latest = None
        self._seq = 0
        self._viewers = 0
        self._stop = threading.Event()
        self.error = None

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
        released = [False]

        def release():
            with self._lock:
                if released[0]:
                    return
                released[0] = True
                self._viewers = max(0, self._viewers - 1)
                if self._viewers == 0:
                    self._stop.set()

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
                return cap.read, cap.release
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
                return (lambda: (True, pc.capture_array())), (lambda: (pc.stop(), pc.close()))
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc))

        raise RuntimeError("; ".join(errs) or f"onbekende camera.backend {backend!r}")

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
            return
        self.error = None
        try:
            while not self._stop.is_set():
                q = int(config.get("camera.jpeg_quality", 55))
                delay = 1.0 / max(1, config.get("camera.fps", 10))
                ok, frame = read()
                if not ok or frame is None:
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
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                print(f"[camera] kon bron niet sluiten: {exc!r}")
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
    })
