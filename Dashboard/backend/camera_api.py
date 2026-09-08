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
                    if idle > 50 and self.error:
                        break
                time.sleep(1.0 / max(1, config.get("camera.fps", 10)))
        finally:
            with self._lock:
                self._viewers -= 1
                if self._viewers <= 0:
                    self._stop.set()


camera = _Camera()


@camera_bp.route("/video_feed")
@require_password
def video_feed():
    if not config.get("camera.enabled", True):
        return Response("camera uit", status=503)
    return Response(
        camera.frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


@camera_bp.route("/api/camera_status")
def camera_status():
    return jsonify({
        "enabled": bool(config.get("camera.enabled", True)),
        "error": camera.error,
        "viewers": camera._viewers,
    })
