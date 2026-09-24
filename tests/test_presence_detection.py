"""Een falende detector is GEEN 'lege kamer'."""
import numpy as np
import pytest

import config
import logic.people_detect as pd
from Dashboard.backend import presence as presence_mod
from Dashboard.backend.presence import PresenceWorker


@pytest.fixture(autouse=True)
def _reset_detector_state(monkeypatch):
    monkeypatch.setattr(pd, "_last_error", None, raising=False)


class _BrokenHog:
    def detectMultiScale(self, *a, **kw):
        raise RuntimeError("OpenCV(4.x) out of memory")


def test_count_people_error_value_is_returned_on_detector_failure(monkeypatch):
    monkeypatch.setattr(pd, "_get_detector", lambda: _BrokenHog())
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    assert pd.count_people(frame) == 0                          # oude contract blijft de default
    assert pd.count_people(frame, error_value=None) is None
    assert pd.count_people(None, error_value=None) is None      # ongeldig frame is ook geen meting
    assert pd.count_people(None) == 0


def test_repeating_detection_error_is_printed_once(monkeypatch, capsys):
    monkeypatch.setattr(pd, "_get_detector", lambda: _BrokenHog())
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    for _ in range(50):
        pd.count_people(frame)
    out = capsys.readouterr().out
    assert out.count("detectiefout") == 1


def _worker(monkeypatch, counts):
    config.set("presence.consecutive_required", 1)
    config.set("presence.empty_grace_s", 20.0)
    config.set("presence.auto_light_enabled", False)
    w = PresenceWorker()
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame, **kw: counts["n"])
    return w, clock


def test_failing_detector_never_turns_an_occupied_room_empty(monkeypatch):
    """count_people() gaf bij een fout 0 -> elke mislukte detectie telde mee voor
    de EMPTY-kant: na de grace-periode ging de lamp uit terwijl er iemand zat
    (en bleef uit, want AAN vereist juist een geslaagde detectie)."""
    counts = {"n": 1}
    w, clock = _worker(monkeypatch, counts)
    w._tick()
    assert w.room_state == "OCCUPIED"

    counts["n"] = None                       # detector kapot
    for _ in range(100):                     # 5 minuten, ver voorbij empty_grace_s
        clock["t"] += 3
        w._tick()
    assert w.room_state == "OCCUPIED"
    assert w.status()["state"] == "unknown"

    counts["n"] = 0                          # detector herstelt, eerste meting leeg
    clock["t"] += 3
    w._tick()
    assert w.room_state == "OCCUPIED", "een enkel leeg frame na een storing maakte de kamer direct leeg"
    assert w.status()["state"] == "present"
    clock["t"] += 21
    w._tick()
    assert w.room_state == "EMPTY"           # pas na een geldige lege periode van de grace


def test_failing_detector_does_not_flap_or_spam_the_log(monkeypatch):
    counts = {"n": 1}
    w, clock = _worker(monkeypatch, counts)
    lines = []
    monkeypatch.setattr(presence_mod, "log", lambda subject, msg: lines.append(msg))
    w._tick()
    counts["n"] = None
    for _ in range(60):
        clock["t"] += 3
        w._tick()
    assert sum("Detection failed" in l for l in lines) == 1, lines
    assert not any("available again" in l for l in lines), lines

    counts["n"] = 1
    clock["t"] += 3
    w._tick()
    assert sum("available again" in l for l in lines) == 1
    assert w.status()["state"] == "present"


def test_failing_detector_does_not_turn_the_lamp_off(monkeypatch):
    """Ook zonder state-overgang mag er geen lamp-actie uit een mislukte meting komen."""
    counts = {"n": 1}
    w, clock = _worker(monkeypatch, counts)
    acted = []
    monkeypatch.setattr(w, "_auto_light", lambda want_on, **kw: acted.append(want_on) or True)
    w._tick()
    assert acted == [True]                   # aan bij binnenkomst
    counts["n"] = None
    for _ in range(50):
        clock["t"] += 3
        w._tick()
    assert acted == [True]


def test_end_to_end_broken_hog_keeps_the_room_occupied(monkeypatch):
    """Zelfde scenario zonder nep-count_people: de echte functie met een kapotte
    HOG-detector (bv. cv2 zonder geheugen)."""
    config.set("presence.consecutive_required", 1)
    config.set("presence.empty_grace_s", 20.0)
    config.set("presence.auto_light_enabled", False)
    w = PresenceWorker()
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    monkeypatch.setattr(w, "_get_frame", lambda: frame)

    w.room_state = "OCCUPIED"
    w._last_positive_at = clock["t"]
    monkeypatch.setattr(pd, "_get_detector", lambda: _BrokenHog())
    for _ in range(40):
        clock["t"] += 3
        w._tick()
    assert w.room_state == "OCCUPIED"
    assert w.status()["state"] == "unknown"
