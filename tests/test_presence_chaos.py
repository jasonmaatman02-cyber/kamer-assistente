"""Eigenschappen-test (willekeurige storingen) voor de aanwezigheidsautomatiek.

Een random reeks van: beeld met persoon / leeg beeld / geen beeld (camera weg) / falende
detector, met willekeurige tijdstappen. Vereisten (niet afgeleid uit de implementatie):

  R1  de lamp gaat nooit UIT binnen `grace` s na een geslaagde detectie van een persoon;
  R2  de lamp gaat nooit UIT binnen `grace` s na herstel van een camera-/detectorstoring
      (een storing is geen "leeg"-meting);
  R3  een lange storing zonder bevestigd leeg beeld laat de kamer 'bezet' (geen lamp-actie);
  R4  na `grace` s aaneengesloten geldig-lege metingen is de kamer weer leeg (liveness).
"""
import random

import pytest

import config
from Dashboard.backend.presence import PresenceWorker

GRACE = 20.0


def _run(seed, monkeypatch, ticks=400):
    rnd = random.Random(seed)
    config.set("presence.consecutive_required", rnd.choice([1, 2, 3]))
    config.set("presence.empty_grace_s", GRACE)
    config.set("presence.auto_light_enabled", True)
    w = PresenceWorker()
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])
    mode = {"m": "empty"}                      # empty | person | none | broken

    monkeypatch.setattr(w, "_get_frame", lambda: None if mode["m"] == "none" else object())
    monkeypatch.setattr("logic.people_detect.count_people",
                        lambda frame, **kw: {"person": rnd.choice([1, 1, 2]), "empty": 0, "broken": None}.get(mode["m"], 0))
    cmds = []
    monkeypatch.setattr(w, "_auto_light", lambda on, **kw: cmds.append((clock["t"], on)) or True)
    import Dashboard.backend.presence as P
    monkeypatch.setattr(P, "is_auto_light_blocked", lambda now=None: False)

    last_positive = None          # sim-tijd van de laatste geslaagde persoon-detectie
    last_recovery = None          # sim-tijd waarop een storing eindigde
    was_disturbed = False
    valid_empty_since = None

    for _ in range(ticks):
        # kies de volgende toestand, met lange runs zodat grace-periodes ook echt verstrijken
        if rnd.random() < 0.15:
            mode["m"] = rnd.choice(["empty", "person", "none", "broken"])
        clock["t"] += rnd.choice([0.5, 1, 3, 3, 3, 8])
        before = len(cmds)
        w._tick()
        m = mode["m"]
        disturbed = m in ("none", "broken")
        if disturbed:
            was_disturbed = True
            valid_empty_since = None
        else:
            if was_disturbed:
                last_recovery = clock["t"]
                was_disturbed = False
            if m == "person":
                last_positive = clock["t"]
                valid_empty_since = None
            else:
                valid_empty_since = valid_empty_since if valid_empty_since is not None else clock["t"]
        for t_cmd, on in cmds[before:]:
            if on is False:
                if last_positive is not None:
                    assert t_cmd - last_positive >= GRACE, f"R1 seed={seed}: UIT {t_cmd - last_positive:.1f}s na persoon"
                if last_recovery is not None:
                    assert t_cmd - last_recovery >= GRACE, f"R2 seed={seed}: UIT {t_cmd - last_recovery:.1f}s na storing"
                assert not disturbed, f"R3 seed={seed}: UIT tijdens een storing"
        if valid_empty_since is not None and clock["t"] - valid_empty_since >= GRACE + 3 and (
                last_positive is None or clock["t"] - last_positive >= GRACE + 3):
            assert w.room_state == "EMPTY", f"R4 seed={seed}: nog bezet na {clock['t'] - valid_empty_since:.0f}s leeg"


@pytest.mark.parametrize("seed", range(60))
def test_presence_survives_random_failures(seed, monkeypatch):
    _run(seed, monkeypatch)
