"""Wekker en routines: geen blokkerende set_alarm tijdens een afgaande wekker,
geen dubbel uitvoeren van dezelfde routine, geen stilzwijgend her-armen van
een al afgegane wekker na een herstart."""
import datetime
import threading
import time

import pytest

import config
from scheduler.alarm import AlarmScheduler


def _in(seconds=0.0, minutes=0):
    return datetime.datetime.now() + datetime.timedelta(seconds=seconds, minutes=minutes)


# --------------------------------------------------------------------------- #
# AlarmScheduler
# --------------------------------------------------------------------------- #
def test_set_alarm_does_not_block_while_the_previous_alarm_is_still_running_its_routine():
    """De wekker-callback (= een hele routine: LLM-groet, TTS, radio; minuten) draait
    IN de wekker-thread. set_alarm() deed daar een join() zonder timeout op, terwijl
    het de lock vasthield: een POST /api/alarm (en cancel) bleef dus minutenlang
    hangen en pinde een waitress-thread per klik."""
    started, release = threading.Event(), threading.Event()

    def routine():
        started.set()
        release.wait(20)

    a = AlarmScheduler(routine)
    a.set_alarm(when=_in(seconds=0.3))
    assert started.wait(5), "wekker ging niet af"

    done = threading.Event()
    result = {}

    def setter():
        result["dt"] = a.set_alarm(when=_in(minutes=10))
        done.set()

    threading.Thread(target=setter, daemon=True).start()
    try:
        assert done.wait(2.0), "set_alarm blokkeert achter de lopende routine"
    finally:
        release.set()

    assert a.alarm_time == result["dt"]                # nieuwe wekker is intact
    time.sleep(0.2)                                     # oude routine is nu klaar
    assert a.alarm_time == result["dt"], "afgelopen wekker wiste de nieuwe wekker"
    assert a.alarm_thread.is_alive()
    a.cancel_alarm()
    assert not a.alarm_thread.is_alive()


def test_cancel_alarm_does_not_block_while_the_routine_is_running():
    started, release = threading.Event(), threading.Event()
    a = AlarmScheduler(lambda: (started.set(), release.wait(20)))
    a.set_alarm(when=_in(seconds=0.3))
    assert started.wait(5)

    done = threading.Event()
    threading.Thread(target=lambda: (a.cancel_alarm(), done.set()), daemon=True).start()
    try:
        assert done.wait(4.0)
    finally:
        release.set()
    assert a.alarm_time is None


def test_set_alarm_from_inside_the_callback_does_not_raise():
    """Een routine die zelf een (nieuwe) wekker zet draait in de wekker-thread:
    join() op de eigen thread gooit RuntimeError."""
    a = AlarmScheduler()
    seen = {}

    def routine():
        try:
            seen["dt"] = a.set_alarm(when=_in(minutes=5))
        except Exception as exc:  # noqa: BLE001
            seen["exc"] = exc

    a.callback = routine
    a.set_alarm(when=_in(seconds=0.2))
    deadline = time.time() + 5
    while not seen and time.time() < deadline:
        time.sleep(0.05)
    assert "exc" not in seen, seen.get("exc")
    assert a.alarm_time == seen["dt"]
    a.cancel_alarm()


def test_replaced_alarm_fires_only_once_at_the_new_time():
    hits = []
    a = AlarmScheduler(lambda: hits.append(time.time()))
    a.set_alarm(when=_in(seconds=0.3))
    a.set_alarm(when=_in(seconds=0.8))       # vervangt de eerste
    time.sleep(1.5)
    assert len(hits) == 1
    a.cancel_alarm()


# --------------------------------------------------------------------------- #
# Dashboard-routes rond de wekker
# --------------------------------------------------------------------------- #
@pytest.fixture()
def routines_api():
    from Dashboard.backend import routines_api as ra

    yield ra
    ra._alarm.cancel_alarm()
    ra._alarm.callback = ra._alarm_fire


def test_dashboard_alarm_after_a_voice_alarm_still_runs_the_selected_routine(client, routines_api):
    """scheduler.alarm_manager.set_alarm(tijd, callback) (spraak-Q&A) overschreef de
    GEDEELDE callback met morning_routine; een daarna vanaf het dashboard gezette
    wekker liep dus stilzwijgend die routine i.p.v. de gekozen (bv. een eigen routine)."""
    from scheduler.alarm_manager import set_alarm as voice_set_alarm

    voice_set_alarm("06:15", lambda: None)
    assert routines_api._alarm.callback is not routines_api._alarm_fire

    r = client.post("/api/alarm", json={"time": "07:30", "routine": "bedtime"})
    assert r.get_json()["success"] is True
    assert routines_api._alarm.callback is routines_api._alarm_fire


def test_alarm_that_already_fired_is_not_rearmed_after_a_restart(client, routines_api, monkeypatch):
    """Na het afgaan meldt de UI 'Geen wekker gezet', maar alarm.time bleef in de
    config staan en werd bij elke herstart (crash, reboot, update) opnieuw
    ingepland -> de volgende ochtend ging de radio ongevraagd aan."""
    ran = []
    monkeypatch.setattr(routines_api, "_run_routine", lambda rid: ran.append(rid))

    assert client.post("/api/alarm", json={"time": "07:30", "routine": "morning"}).get_json()["success"]
    assert config.get("alarm.armed") is True

    routines_api._alarm_fire()
    assert ran == ["morning"]
    assert config.get("alarm.armed") is False
    assert config.get("alarm.time") == "07:30"           # blijft staan als voorinvulling in de UI
    assert routines_api._should_rearm_saved_alarm() is False


def test_saved_alarm_that_has_not_fired_yet_is_rearmed_and_legacy_config_too(routines_api):
    config.set("alarm.time", "07:30")
    config.set("alarm.armed", True)
    assert routines_api._should_rearm_saved_alarm() is True
    config.set("alarm.armed", False)
    assert routines_api._should_rearm_saved_alarm() is False
    config.set("alarm.time", "")
    config.set("alarm.armed", True)
    assert routines_api._should_rearm_saved_alarm() is False        # niets om te her-armen

    # oude settings.json zonder 'armed'-sleutel: gedrag van voor deze wijziging behouden
    cfg = config.get("alarm")
    cfg.pop("armed", None)
    config.set("alarm", {**cfg, "time": "07:30"})
    config.reload()
    assert routines_api._should_rearm_saved_alarm() is True


def test_clearing_the_alarm_disarms_it(client, routines_api):
    client.post("/api/alarm", json={"time": "07:30", "routine": "morning"})
    client.delete("/api/alarm")
    assert config.get("alarm.armed") is False
    assert routines_api._should_rearm_saved_alarm() is False


# --------------------------------------------------------------------------- #
# Dezelfde routine niet twee keer tegelijk
# --------------------------------------------------------------------------- #
def test_same_routine_is_not_run_twice_concurrently(client, routines_api, monkeypatch):
    """Dubbelklik / twee tabbladen / wekker tijdens een handmatige run: twee
    tegelijk lopende ochtend-routines = dubbele TTS, dubbele radio-start, twee
    vastgezette waitress-threads."""
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow(rid):
        calls.append(rid)
        started.set()
        release.wait(20)

    monkeypatch.setattr(routines_api, "_run_routine_unguarded", slow)

    first = {}
    t = threading.Thread(
        target=lambda: first.update(r=routines_api._run_routine("morning")),  # bv. de wekker
        daemon=True,
    )
    t.start()
    assert started.wait(5)

    r2 = client.post("/api/routines/run", json={"id": "morning"})
    assert r2.status_code == 409
    assert r2.get_json()["success"] is False

    # een andere routine mag wel gewoon
    r3 = client.post("/api/routines/run", json={"id": "desk"})
    assert r3.status_code != 409

    release.set()
    t.join(5)
    assert not t.is_alive()
    assert calls.count("morning") == 1

    # na afloop kan 'ie weer
    assert client.post("/api/routines/run", json={"id": "morning"}).status_code == 200
    assert calls.count("morning") == 2


def test_routine_lock_is_released_after_an_exception(client, routines_api, monkeypatch):
    def boom(rid):
        raise RuntimeError("stuk")

    monkeypatch.setattr(routines_api, "_run_routine_unguarded", boom)
    assert client.post("/api/routines/run", json={"id": "morning"}).status_code == 500
    monkeypatch.setattr(routines_api, "_run_routine_unguarded", lambda rid: None)
    assert client.post("/api/routines/run", json={"id": "morning"}).status_code == 200


# --------------------------------------------------------------------------- #
# Klokstap (Pi zonder batterijklok: NTP springt de tijd na de start vooruit)
# --------------------------------------------------------------------------- #
class _Clock:
    """Nep-klok voor _wait_for_alarm: wandklok + monotone klok in een keer te verschuiven."""

    def __init__(self, wall):
        import types

        self.wall = wall
        self.mono = 1000.0
        clock = self

        class _DT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.wall

        self.datetime = types.SimpleNamespace(datetime=_DT, timedelta=datetime.timedelta)
        self.time = types.SimpleNamespace(time=lambda: clock.wall.timestamp(), monotonic=lambda: clock.mono)

    def advance(self, seconds, step=0.0):
        """Echte tijd ``seconds`` verder; ``step`` extra alleen op de wandklok (de klokstap)."""
        self.mono += seconds
        self.wall += datetime.timedelta(seconds=seconds + step)


class _Stop:
    def __init__(self, clock, steps, cancel_after):
        self.clock, self.steps, self.cancel_after, self.calls = clock, list(steps), cancel_after, 0

    def is_set(self):
        return self.calls > self.cancel_after

    def wait(self, timeout):
        self.calls += 1
        self.clock.advance(timeout, self.steps.pop(0) if self.steps else 0.0)
        return self.calls > self.cancel_after


def _run_wait(monkeypatch, start, jump_to=None, cancel_after=6, tod=(7, 0)):
    """Draai _wait_for_alarm met een nep-klok. ``jump_to``: de wandklok springt bij de eerste wachtronde
    (30 s echte tijd) naar dit moment, zonder dat de monotone klok meeloopt."""
    import scheduler.alarm as A

    clock = _Clock(start)
    monkeypatch.setattr(A, "datetime", clock.datetime)
    monkeypatch.setattr(A, "time", clock.time)
    steps = [(jump_to - (start + datetime.timedelta(seconds=30))).total_seconds()] if jump_to else []
    fired = []
    a = AlarmScheduler(lambda: fired.append(clock.wall))
    alarm_dt = start.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
    if alarm_dt <= start:
        alarm_dt += datetime.timedelta(days=1)
    a.alarm_time = alarm_dt
    a._wait_for_alarm(_Stop(clock, steps, cancel_after), alarm_dt, tod, (start.timestamp(), clock.mono))
    return a, fired, alarm_dt


def test_clock_step_far_past_the_alarm_moves_it_to_tomorrow_instead_of_firing_late(monkeypatch):
    """Pi start om 22:00 (oude tijd), wekker 07:00; NTP zet de klok naar 10:00 de volgende dag."""
    a, fired, _ = _run_wait(monkeypatch, datetime.datetime(2026, 9, 23, 22, 0),
                            jump_to=datetime.datetime(2026, 9, 24, 10, 0))
    assert fired == [], "wekker ging om 10:00 af omdat de klok sprong"
    assert a.alarm_time == datetime.datetime(2026, 9, 25, 7, 0)


def test_clock_step_just_after_the_alarm_time_still_fires(monkeypatch):
    """Hooguit 10 minuten te laat (klok springt naar 07:04): dan is de wekker alsnog welkom."""
    a, fired, _ = _run_wait(monkeypatch, datetime.datetime(2026, 9, 23, 22, 0),
                            jump_to=datetime.datetime(2026, 9, 24, 7, 4))
    assert len(fired) == 1 and (fired[0].hour, fired[0].minute) == (7, 4)
    assert a.alarm_time is None


def test_clock_step_backwards_keeps_todays_alarm(monkeypatch):
    a, fired, _ = _run_wait(monkeypatch, datetime.datetime(2026, 9, 24, 6, 0),
                            jump_to=datetime.datetime(2026, 9, 24, 3, 0))
    assert fired == []
    assert a.alarm_time == datetime.datetime(2026, 9, 24, 7, 0)


def test_no_step_means_no_change_and_it_fires_on_time(monkeypatch):
    a, fired, dt = _run_wait(monkeypatch, datetime.datetime(2026, 9, 24, 6, 59), cancel_after=50)
    assert len(fired) == 1 and dt <= fired[0] < dt + datetime.timedelta(seconds=31)
    assert a.alarm_time is None


def test_after_clock_step_rules():
    step = AlarmScheduler._after_clock_step
    d = datetime.datetime
    assert step((7, 0), d(2026, 9, 24, 6, 0)) == d(2026, 9, 24, 7, 0)      # nog te komen: vandaag
    assert step((7, 0), d(2026, 9, 24, 7, 4)) == d(2026, 9, 24, 7, 0)      # 4 min te laat: alsnog vandaag
    assert step((7, 0), d(2026, 9, 24, 7, 11)) == d(2026, 9, 25, 7, 0)     # >10 min te laat: morgen
    assert step((7, 0), d(2026, 9, 24, 10, 0)) == d(2026, 9, 25, 7, 0)
