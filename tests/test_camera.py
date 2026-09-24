"""Camera-thread (Dashboard/backend/camera_api.py::_Camera) zonder hardware:
een gescripte nep-cv2 simuleert een USB-camera die wegvalt, terugkomt en
hangt. Doel: geen hamer-loop op een ontbrekende camera, automatisch herstel,
geen bevroren 'vers' frame, geen onnodig hoge framerate als alleen presence
meekijkt, en geen thread-/resource-lek bij herhaald starten/stoppen."""
import sys
import threading
import time
import types

import pytest


class _Buf:
    def tobytes(self):
        return b"\xff\xd8jpeg"


class FakeSource:
    """Beheert de scripted levensloop van opeenvolgende VideoCapture-opens."""

    def __init__(self, open_results=None, frames_before_fail=None):
        self.open_results = list(open_results or [])   # per open: True/False; leeg -> True
        self.frames_before_fail = frames_before_fail   # None = oneindig
        self.opens = 0
        self.releases = 0
        self.reads = 0
        self.lock = threading.Lock()

    def make_cap(self, idx):
        with self.lock:
            self.opens += 1
            ok = self.open_results.pop(0) if self.open_results else True
        return _Cap(self, ok)


class _Cap:
    def __init__(self, src, opened):
        self.src = src
        self._opened = opened
        self._served = 0

    def set(self, *a):
        pass

    def isOpened(self):
        return self._opened

    def read(self):
        with self.src.lock:
            self.src.reads += 1
        if self.src.frames_before_fail is not None and self._served >= self.src.frames_before_fail:
            return False, None
        self._served += 1
        return True, object()

    def release(self):
        with self.src.lock:
            self.src.releases += 1


def _install_fake_cv2(monkeypatch, src):
    cv2 = types.ModuleType("cv2")
    cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_FPS = 3, 4, 5
    cv2.IMWRITE_JPEG_QUALITY = 1
    cv2.VideoCapture = src.make_cap
    cv2.resize = lambda frame, size: frame
    cv2.imencode = lambda ext, frame, params: (True, _Buf())
    monkeypatch.setitem(sys.modules, "cv2", cv2)


@pytest.fixture()
def cam_mod(monkeypatch):
    import config
    import Dashboard.backend.camera_api as m

    config.set("camera.backend", "opencv")     # nooit picamera2 proberen
    config.set("camera.fps", 50)
    return m


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _join(cam, timeout=3.0):
    t = cam._thread
    if t is not None:
        t.join(timeout)
        assert not t.is_alive(), "capture-thread stopte niet"


# --------------------------------------------------------------------------- #
# 1. Ontbrekende camera: backoff i.p.v. elke presence-tick opnieuw openen
# --------------------------------------------------------------------------- #
def test_missing_camera_is_not_reopened_every_tick(monkeypatch, cam_mod):
    src = FakeSource(open_results=[False] * 50)
    _install_fake_cv2(monkeypatch, src)
    clock = {"t": 1000.0}
    monkeypatch.setattr(cam_mod, "_mono", lambda: clock["t"])
    cam = cam_mod._Camera()

    def tick():
        cam._ensure_running()
        _join(cam)

    tick()                       # 1e mislukking: USB-hikje -> direct opnieuw toegestaan
    tick()                       # 2e mislukking -> nu 5s backoff
    assert src.opens == 2
    for _ in range(20):          # presence-ticks van 3s binnen de backoff-tijd: niks
        clock["t"] += 0.2
        tick()
    assert src.opens == 2, "camera werd binnen de backoff toch opnieuw geopend"

    clock["t"] += 5                # backoff verlopen -> 3e poging (daarna 10s)
    tick()
    assert src.opens == 3
    clock["t"] += 9.9
    tick()
    assert src.opens == 3
    clock["t"] += 0.2
    tick()
    assert src.opens == 4


def test_backoff_is_capped(monkeypatch, cam_mod):
    src = FakeSource(open_results=[False] * 100)
    _install_fake_cv2(monkeypatch, src)
    clock = {"t": 0.0}
    monkeypatch.setattr(cam_mod, "_mono", lambda: clock["t"])
    cam = cam_mod._Camera()
    for _ in range(12):
        clock["t"] += 1000          # ruim voorbij elke backoff
        cam._ensure_running()
        _join(cam)
    assert cam._retry_at - clock["t"] <= cam_mod._BACKOFF_MAX_S


# --------------------------------------------------------------------------- #
# 2. Herstel na read-fout / reconnect
# --------------------------------------------------------------------------- #
def test_camera_recovers_after_usb_disconnect(monkeypatch, cam_mod):
    src = FakeSource(frames_before_fail=3)       # levert 3 frames, dan "USB eruit"
    _install_fake_cv2(monkeypatch, src)
    cam = cam_mod._Camera()
    monkeypatch.setattr(cam, "_frame_delay", lambda: 0.01)

    cam.keep_alive(True)
    _join(cam)                                   # read faalt -> thread stopt netjes
    assert cam.error == "geen beeld van camera"
    assert src.releases == 1, "bron niet vrijgegeven na read-fout"
    assert cam.latest_jpeg() is None, "na uitval mag er geen oud frame blijven hangen"
    assert cam._consec_fail == 1

    src.frames_before_fail = None                # camera weer aangesloten
    cam._ensure_running()                        # presence-tick: 1e mislukking -> direct herstel
    assert _wait(lambda: cam.latest_jpeg() is not None), "geen herstel na reconnect"
    assert cam.error is None
    assert cam._consec_fail == 0, "gezonde bron moet de backoff resetten"
    cam.release()
    _join(cam)
    assert src.releases == 2


def test_no_thread_or_handle_leak_over_many_restarts(monkeypatch, cam_mod):
    src = FakeSource()
    _install_fake_cv2(monkeypatch, src)
    cam = cam_mod._Camera()
    monkeypatch.setattr(cam, "_frame_delay", lambda: 0.005)
    baseline = threading.active_count()

    for _ in range(15):
        cam.keep_alive(True)
        assert _wait(lambda: cam.latest_jpeg() is not None)
        cam.release()
        _join(cam)
    cam.keep_alive(False)

    assert src.opens == src.releases == 15, f"opens={src.opens} releases={src.releases}"
    assert threading.active_count() <= baseline, "capture-threads stapelen zich op"


# --------------------------------------------------------------------------- #
# 3. Bevroren frame mag niet 'vers' lijken
# --------------------------------------------------------------------------- #
def test_stale_frame_is_reported_as_no_frame(monkeypatch, cam_mod):
    clock = {"t": 500.0}
    monkeypatch.setattr(cam_mod, "_mono", lambda: clock["t"])
    cam = cam_mod._Camera()
    cam._latest = (b"jpeg", 1, clock["t"])

    assert cam.latest_jpeg(max_age_s=10) == b"jpeg"
    clock["t"] += 9
    assert cam.latest_jpeg(max_age_s=10) == b"jpeg"
    clock["t"] += 2                      # 11s oud
    assert cam.latest_jpeg(max_age_s=10) is None
    assert cam.latest_jpeg() == b"jpeg", "zonder max_age_s (MJPEG/snapshot) blijft het gedrag gelijk"
    assert cam.frame_age_s() == 11.0


# --------------------------------------------------------------------------- #
# 4. Framerate: presence-only laag, echte kijker meteen vol
# --------------------------------------------------------------------------- #
def test_presence_only_capture_runs_at_reduced_rate(monkeypatch, cam_mod):
    import config

    config.set("presence.interval_s", 0.8)                    # -> 0.4s tussen frames
    monkeypatch.setattr(cam_mod, "_PRESENCE_ONLY_MIN_S", 0.2)
    src = FakeSource()
    _install_fake_cv2(monkeypatch, src)
    cam = cam_mod._Camera()

    hold = cam.acquire()                    # presence houdt precies één plek vast
    cam.keep_alive(True)
    time.sleep(1.2)
    reads_low = src.reads
    cam.release()
    hold()
    _join(cam)

    # camera.fps=50 zou ~60 reads in 1.2s zijn; presence-only hoort rond de 3-4 te zitten
    assert 1 <= reads_low <= 8, f"{reads_low} reads in 1.2s -- niet gereduceerd"


def test_real_viewer_gets_full_rate_immediately(monkeypatch, cam_mod):
    import config

    config.set("presence.interval_s", 6.0)                    # -> 2.0s wachttijd in presence-only
    src = FakeSource()
    _install_fake_cv2(monkeypatch, src)
    cam = cam_mod._Camera()

    hold = cam.acquire()
    cam.keep_alive(True)
    assert _wait(lambda: src.reads >= 1)
    time.sleep(0.3)
    low = src.reads
    assert low <= 2                        # thread slaapt nu ~2s tussen frames

    viewer = cam.acquire()                 # browser opent de MJPEG-stream -> wake + volle fps
    before = src.reads
    time.sleep(0.6)
    gained = src.reads - before
    assert gained >= 10, f"kijker kreeg maar {gained} frames in 0.6s -- wake/framerate werkt niet"

    viewer()
    hold()
    cam.release()
    _join(cam)


def test_release_wakes_a_sleeping_capture_thread_promptly(monkeypatch, cam_mod):
    import config

    config.set("presence.interval_s", 30.0)                   # -> 2.0s (maximum) slaap
    src = FakeSource()
    _install_fake_cv2(monkeypatch, src)
    cam = cam_mod._Camera()
    hold = cam.acquire()
    cam.keep_alive(True)
    assert _wait(lambda: src.reads >= 1)
    t0 = time.monotonic()
    cam.release()
    hold()
    _join(cam)
    assert time.monotonic() - t0 < 1.0, "stop wachtte de hele frame-slaap uit"


# --------------------------------------------------------------------------- #
# 5. Presence-kant: herstel-grace en log-spam
# --------------------------------------------------------------------------- #
def test_presence_grace_restarts_after_sensor_recovery(monkeypatch):
    """Na een lange camerastoring (geen meting) mag één negatief frame de kamer
    niet direct 'leeg' maken: de grace-periode moet vanaf het herstel lopen."""
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.consecutive_required", 1)
    config.set("presence.empty_grace_s", 20.0)
    config.set("presence.auto_light_enabled", False)
    w = PresenceWorker()
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])
    counts = {"n": 1}
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame, **kw: counts["n"])

    monkeypatch.setattr(w, "_get_frame", lambda: object())
    w._tick()
    assert w.room_state == "OCCUPIED"

    monkeypatch.setattr(w, "_get_frame", lambda: None)          # camera 10 minuten weg
    for _ in range(200):
        clock["t"] += 3
        w._tick()
    assert w.room_state == "OCCUPIED" and w.status()["state"] == "unknown"

    counts["n"] = 0
    monkeypatch.setattr(w, "_get_frame", lambda: object())     # camera terug, eerste beeld leeg
    clock["t"] += 3
    w._tick()
    assert w.room_state == "OCCUPIED", "1 negatief frame na een storing maakte de kamer direct leeg"

    clock["t"] += 10
    w._tick()
    assert w.room_state == "OCCUPIED"                           # grace (20s) nog niet om
    clock["t"] += 11
    w._tick()
    assert w.room_state == "EMPTY"                              # nu wel: 21s echt leeg gemeten


def test_presence_passes_staleness_limit_to_camera(monkeypatch):
    import types as _t

    from Dashboard.backend.presence import PresenceWorker
    import config

    config.set("presence.interval_s", 3.0)
    seen = {}
    fake_camera = _t.SimpleNamespace(
        acquire=lambda: (lambda: None),
        keep_alive=lambda on: None,
        latest_jpeg=lambda max_age_s=None: seen.setdefault("max_age_s", max_age_s) and None,
    )
    import Dashboard.backend.camera_api as camera_mod
    monkeypatch.setattr(camera_mod, "camera", fake_camera)

    w = PresenceWorker()
    assert w._get_frame() is None
    assert seen["max_age_s"] is not None and seen["max_age_s"] >= 10.0


def test_presence_loop_does_not_spam_the_log_with_a_repeating_error(monkeypatch):
    import config
    from Dashboard.backend.presence import PresenceWorker
    import Dashboard.backend.presence as presence_mod

    config.set("presence.enabled", True)
    w = PresenceWorker()
    monkeypatch.setattr(w, "_tick", lambda: (_ for _ in ()).throw(RuntimeError("kapotte camera-driver")))
    lines = []
    monkeypatch.setattr(presence_mod, "log", lambda subject, msg: lines.append(msg))

    for _ in range(100):
        assert w._run_once() >= 0.5      # thread blijft draaien, geeft gewoon een wachttijd terug
    assert len(lines) == 1, f"{len(lines)} identieke foutregels"

    monkeypatch.setattr(w, "_tick", lambda: (_ for _ in ()).throw(ValueError("iets anders")))
    w._run_once()
    assert len(lines) == 2, "een ANDERE fout moet wel gelogd worden"


def test_presence_tick_passes_clamped_detect_scale_and_reports_timing(monkeypatch):
    import config
    from Dashboard.backend.presence import PresenceWorker

    w = PresenceWorker()
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    seen = {}

    def fake_count(frame, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr("logic.people_detect.count_people", fake_count)

    w._tick()
    assert seen["scale"] == 1.0, "default moet ongewijzigd gedrag zijn"

    config.set("presence.detect_scale", 0.6)
    w._tick()
    assert seen["scale"] == 0.6

    for wild, expected in ((0.0, 1.0), (5.0, 1.0), (0.01, 0.25), (-2, 1.0)):
        config.set("presence.detect_scale", wild)
        w._tick()
        assert 0.25 <= seen["scale"] <= 1.0, f"{wild} -> {seen['scale']}"
    assert isinstance(w.status()["detect_ms"], float)
