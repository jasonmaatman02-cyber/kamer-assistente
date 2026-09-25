"""Een herstart van de service (deploy, watchdog, crash, reboot) terwijl er iemand in de kamer zit is
GEEN nieuwe binnenkomst: de lamp mag niet opnieuw op 100% worden gezet (aan() wist een gedimde of
gekleurde stand), maar staat 'ie uit, dan gaat 'ie wel aan (bv. na een stroomstoring)."""
import pytest

import config
import Dashboard.backend.presence as P
from Dashboard.backend.presence import PresenceWorker


class FakeLamp:
    def __init__(self, on):
        self.on = on
        self.calls = []

    async def status(self):
        self.calls.append("status")
        return {"on": self.on}

    async def aan(self):
        self.calls.append("aan")
        self.on = True

    async def uit(self):
        self.calls.append("uit")
        self.on = False


@pytest.fixture()
def rig(monkeypatch):
    config.set("presence.consecutive_required", 2)
    config.set("presence.empty_grace_s", 10.0)
    config.set("presence.auto_light_enabled", True)
    config.set("presence.auto_light_block_after", "")
    w = PresenceWorker()
    clock = {"t": 1_000_000.0}
    counts = {"n": 0}
    lamp = {"obj": FakeLamp(on=False)}
    monkeypatch.setattr("time.time", lambda: clock["t"])
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    monkeypatch.setattr("logic.people_detect.count_people", lambda frame, **kw: counts["n"])
    monkeypatch.setattr(P.S, "lamp_ip", lambda x: "192.0.2.9")
    monkeypatch.setattr(P.S, "lamp", lambda ip: lamp["obj"])
    monkeypatch.setattr(P, "is_auto_light_blocked", lambda now=None: False)

    def tick(n=None, dt=3.0):
        if n is not None:
            counts["n"] = n
        clock["t"] += dt
        w._tick()

    return w, tick, lamp, clock


def test_restart_while_occupied_leaves_a_lamp_that_is_already_on_alone(rig):
    w, tick, lamp, _ = rig
    lamp["obj"] = FakeLamp(on=True)                       # bv. gedimd door de gebruiker
    tick(1)
    tick(1)                                                # 2 opeenvolgende metingen -> OCCUPIED
    assert w.room_state == "OCCUPIED"
    assert lamp["obj"].calls == ["status"], "aan() zette de helderheid weer op 100%"


def test_restart_while_occupied_turns_the_lamp_on_if_it_is_off(rig):
    """Bv. na een stroomstoring: Pi en lamp starten opnieuw, er zit iemand in het donker."""
    w, tick, lamp, _ = rig
    tick(1)
    tick(1)
    assert w.room_state == "OCCUPIED"
    assert lamp["obj"].calls == ["status", "aan"] and lamp["obj"].on is True


def test_a_real_arrival_into_an_empty_room_still_turns_the_lamp_on_unconditionally(rig):
    w, tick, lamp, _ = rig
    tick(0)                                               # basislijn: leeg bij de start
    lamp["obj"] = FakeLamp(on=True)
    tick(1)
    tick(1)
    assert w.room_state == "OCCUPIED"
    assert lamp["obj"].calls == ["aan"]                   # normaal gedrag: geen status-vraag, gewoon aan


def test_a_passer_by_at_start_does_not_disable_the_next_real_arrival(rig):
    """Eerste meting positief, dan leeg: de 'al aanwezig bij de start'-reeks is doorbroken."""
    w, tick, lamp, _ = rig
    tick(1)
    tick(0)
    assert w.room_state == "EMPTY" and lamp["obj"].calls == []
    lamp["obj"] = FakeLamp(on=True)
    tick(1)
    tick(1)
    assert lamp["obj"].calls == ["aan"]


def test_leaving_after_a_baseline_start_turns_the_lamp_off_normally(rig):
    w, tick, lamp, _ = rig
    lamp["obj"] = FakeLamp(on=True)
    tick(1)
    tick(1)
    lamp["obj"].calls.clear()
    tick(0)
    tick(0, dt=11)                                        # voorbij de grace
    assert w.room_state == "EMPTY" and lamp["obj"].calls == ["uit"]
    lamp["obj"].calls.clear()
    tick(1)
    tick(1)                                               # en de volgende binnenkomst is weer gewoon
    assert lamp["obj"].calls == ["aan"]


def test_baseline_start_respects_the_2130_rule(rig, monkeypatch):
    w, tick, lamp, _ = rig
    monkeypatch.setattr(P, "is_auto_light_blocked", lambda now=None: True)
    tick(1)
    tick(1)
    assert w.room_state == "OCCUPIED" and lamp["obj"].calls == []      # niets, zelfs niet 'status'


def test_failing_status_call_falls_back_to_the_normal_retry_path(rig):
    w, tick, lamp, _ = rig

    class Broken(FakeLamp):
        async def status(self):
            raise RuntimeError("time-out")

    lamp["obj"] = Broken(on=False)
    tick(1)
    tick(1)
    assert w.room_state == "OCCUPIED"
    assert w._pending_light is True                        # mislukt -> de gewone retry pakt het op
    lamp["obj"] = FakeLamp(on=False)
    tick(1)                                                # volgende tick: retry (nu zonder status-check)
    assert lamp["obj"].calls == ["aan"] and w._pending_light is None


def test_unknown_sensor_before_the_first_measurement_does_not_set_the_baseline(rig, monkeypatch):
    w, tick, lamp, _ = rig
    monkeypatch.setattr(w, "_get_frame", lambda: None)
    tick()
    tick()
    assert w._baseline is None
    monkeypatch.setattr(w, "_get_frame", lambda: object())
    tick(0)
    assert w._baseline is False
