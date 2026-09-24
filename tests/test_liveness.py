"""Watchdog-liveness: een GEDEELTELIJKE vriezer (presence-lus, TTS-lock, AI-slots) moet zichtbaar zijn,
ook al antwoordt /api/config nog gewoon."""
import threading
import time

import pytest

import config
from Dashboard.backend import watchdog as W
from Dashboard.backend.presence import PresenceWorker


# --------------------------------------------------------------------------- #
# presence
# --------------------------------------------------------------------------- #
def _running_worker(monkeypatch):
    config.set("presence.enabled", True)
    config.set("presence.interval_s", 0.5)
    w = PresenceWorker()
    monkeypatch.setattr(w, "_tick", lambda: None)
    w.start()
    deadline = time.time() + 3
    while w._thread is None or not w._thread.is_alive():
        assert time.time() < deadline
        time.sleep(0.01)
    return w


def test_presence_is_healthy_while_the_loop_runs(monkeypatch):
    w = _running_worker(monkeypatch)
    try:
        ok, detail = w.liveness()
        assert ok, detail
    finally:
        w._stop.set()
        w._thread.join(3)


def test_presence_reported_stuck_when_a_round_never_finishes(monkeypatch):
    """Een hangende detectie/lamp-aanroep: de thread leeft, maar de lus komt niet meer rond."""
    config.set("presence.enabled", True)
    config.set("presence.interval_s", 0.5)
    w = PresenceWorker()
    release = threading.Event()
    entered = threading.Event()

    def hang():
        entered.set()
        release.wait(30)

    monkeypatch.setattr(w, "_tick", hang)
    w.start()
    assert entered.wait(3)
    try:
        beat = w._beat
        ok, _ = w.liveness(now=beat + 30)            # nog binnen de grens (300 s)
        assert ok
        ok, detail = w.liveness(now=beat + 400)
        assert not ok and "reageert al" in detail
        assert W.internal_failures([("presence", lambda: w.liveness(now=beat + 400))]) == [f"presence: {detail}"]
    finally:
        release.set()
        w._stop.set()
        w._thread.join(5)


def test_presence_liveness_limit_scales_with_a_slow_interval(monkeypatch):
    config.set("presence.enabled", True)
    config.set("presence.interval_s", 30.0)           # 20 x 30 = 600 s
    w = PresenceWorker()
    w._thread = threading.Thread(target=lambda: time.sleep(5), daemon=True)
    w._thread.start()
    beat = w._beat
    assert w.liveness(now=beat + 500)[0] is True
    assert w.liveness(now=beat + 700)[0] is False


def test_dead_presence_thread_is_restarted_not_reported(monkeypatch):
    config.set("presence.enabled", True)
    w = PresenceWorker()
    monkeypatch.setattr(w, "_tick", lambda: None)
    assert w._thread is None
    ok, detail = w.liveness()
    assert ok and "herstart" in detail
    assert w._thread is not None
    w._stop.set()
    w._thread.join(5)


def test_presence_disabled_is_always_healthy():
    config.set("presence.enabled", False)
    w = PresenceWorker()
    assert w.liveness(now=w._beat + 10_000) == (True, "presence uit")


# --------------------------------------------------------------------------- #
# tts
# --------------------------------------------------------------------------- #
def test_tts_lock_held_too_long_is_reported(monkeypatch):
    import ai.tts as tts

    assert tts.liveness()[0] is True
    monkeypatch.setattr(tts, "_speaking_since", 1000.0)
    assert tts.liveness(now=1100.0)[0] is True
    ok, detail = tts.liveness(now=1000.0 + tts._SPEAK_STUCK_S + 1)
    assert not ok and "speak()" in detail


def test_speaking_marker_is_set_during_and_cleared_after_speak(monkeypatch):
    import ai.tts as tts

    config.set("tts.backend", "espeak")
    seen = {}

    def fake(text, backend):
        seen["during"] = tts._speaking_since
        raise RuntimeError("stuk")

    monkeypatch.setattr(tts, "_speak_locked", fake)
    with pytest.raises(RuntimeError):
        tts.speak("hoi")
    assert seen["during"] is not None
    assert tts._speaking_since is None                # ook na een exceptie opgeruimd
    tts.speak("")                                     # lege tekst: geen marker
    assert tts._speaking_since is None


# --------------------------------------------------------------------------- #
# ai-slots
# --------------------------------------------------------------------------- #
def test_all_ai_slots_busy_for_too_long_is_a_leak(monkeypatch):
    from logic import gpt_handler as gh

    monkeypatch.setattr(gh, "_slots_full_since", None)
    monkeypatch.setattr(gh, "_llm_slots", threading.BoundedSemaphore(2))
    assert gh.liveness(now=0.0) == (True, "2/2 AI-slots vrij")
    gh._llm_slots.acquire()
    assert gh.liveness(now=10.0)[0] is True           # 1 vrij
    gh._llm_slots.acquire()
    assert gh.liveness(now=20.0)[0] is True           # alles bezet, net begonnen
    assert gh.liveness(now=20.0 + gh._SLOTS_STUCK_S - 1)[0] is True
    ok, detail = gh.liveness(now=20.0 + gh._SLOTS_STUCK_S + 1)
    assert not ok and "AI-slots" in detail
    gh._llm_slots.release()                           # een slot komt terug -> weer gezond, teller reset
    assert gh.liveness(now=99999.0)[0] is True
    gh._llm_slots.acquire()
    assert gh.liveness(now=100000.0)[0] is True       # opnieuw vanaf nul geteld


# --------------------------------------------------------------------------- #
# watchdog-samenstelling
# --------------------------------------------------------------------------- #
def test_internal_failures_collects_only_unhealthy_checks_and_ignores_broken_ones(capsys):
    def boom():
        raise RuntimeError("check kapot")

    checks = [("a", lambda: (True, "ok")), ("b", lambda: (False, "vast")), ("c", boom)]
    assert W.internal_failures(checks) == ["b: vast"]
    assert "check 'c' zelf mislukt" in capsys.readouterr().out


def test_probe_fails_when_an_internal_check_fails_even_though_http_is_fine(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "/x")
    monkeypatch.setenv("WATCHDOG_USEC", "3000000")
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    captured = {}

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            captured["probe"] = args[1]

        def start(self):
            pass

    monkeypatch.setattr(W.threading, "Thread", FakeThread)
    monkeypatch.setattr(W, "http_probe", lambda port: True)

    monkeypatch.setattr(W, "internal_failures", lambda checks=None: [])
    assert W.start(5000) is not None
    assert captured["probe"]() is True

    monkeypatch.setattr(W, "internal_failures", lambda checks=None: ["presence: vast"])
    assert captured["probe"]() is False               # HTTP ok, maar presence hangt -> geen ping

    monkeypatch.setattr(W, "http_probe", lambda port: False)
    monkeypatch.setattr(W, "internal_failures", lambda checks=None: [])
    assert captured["probe"]() is False


def test_health_endpoint_reports_liveness(client):
    d = client.get("/api/health").get_json()
    live = d["liveness"]
    assert {"presence", "tts", "ai"} <= set(live)
    assert all(isinstance(v["ok"], bool) and v["detail"] for v in live.values())
