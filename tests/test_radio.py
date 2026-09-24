"""RadioPlayer.play() moet niet meer altijd blind 4s wachten (zie
sound_system/radio.py) -- alleen zolang de stream daadwerkelijk nog niet
speelt. Bouwt geen echte RadioPlayer (vlc.Instance() heeft een werkende
libvlc nodig); test tegen een kale instantie met een nep-player."""
import time

import vlc

from sound_system.radio import RadioPlayer


class FakePlayer:
    def __init__(self, states):
        self._states = list(states)
        self.played = 0
        self.volume = None
        self.media = None
        self.stopped = False

    def is_playing(self):
        return False

    def stop(self):
        self.stopped = True

    def audio_set_volume(self, v):
        self.volume = v

    def set_media(self, m):
        self.media = m

    def play(self):
        self.played += 1

    def get_state(self):
        # Elke aanroep 'schuift' naar de volgende staat op, blijft op de
        # laatste hangen zodra ze op zijn.
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]


class FakeInstance:
    def media_new(self, url):
        return object()


def _player(states) -> RadioPlayer:
    """Een RadioPlayer zonder de echte __init__ (geen libvlc nodig)."""
    p = object.__new__(RadioPlayer)
    p.instance = FakeInstance()
    p.player = FakePlayer(states)
    p.start_time = None
    p.current_station_name = None
    p.stations = {"radio538": "http://example.invalid/stream.mp3"}
    return p


def test_play_returns_fast_once_stream_is_playing():
    p = _player([vlc.State.Buffering, vlc.State.Buffering, vlc.State.Playing])
    start = time.monotonic()
    result = p.play("radio538")
    elapsed = time.monotonic() - start

    assert result == "Speelt nu: radio538"
    assert elapsed < 1.0, f"had niet de volle 2s+ moeten wachten, duurde {elapsed}s"
    assert p.player.played == 1, "geen tweede play()-poging nodig als de eerste al lukt"


def test_play_retries_once_then_reports_failure_if_never_playing():
    p = _player([vlc.State.Error])
    result = p.play("radio538")

    assert result == "Radio kon niet starten (check URL of internet)."
    assert p.player.played == 2, "moet één herstart-poging doen voor het opgeeft"


def test_play_unknown_station_does_not_touch_player():
    p = _player([vlc.State.Playing])
    result = p.play("nietbestaand")

    assert "niet gevonden" in result
    assert p.player.played == 0


# --------------------------------------------------------------------------- #
# Eén gedeelde speler voor dashboard, AI-tools en wekker
# --------------------------------------------------------------------------- #
def test_shared_player_is_one_instance_for_dashboard_ai_and_alarm(monkeypatch):
    """Voorheen maakte elk zijn eigen RadioPlayer (de ochtendroutine zelfs bij elke run een nieuwe): wat de
    wekker of de chat startte kon niet met de Stop-knop van het dashboard worden gestopt."""
    import sound_system.radio as radio_mod

    created = []

    class Fake:
        def __init__(self):
            created.append(self)

    monkeypatch.setattr(radio_mod, "_shared", None)
    monkeypatch.setattr(radio_mod, "RadioPlayer", Fake)
    a = radio_mod.shared_player()
    b = radio_mod.shared_player()
    assert a is b and len(created) == 1

    from Dashboard.backend import services as S
    import logic.gpt_handler as gh
    import scheduler.routines as R

    monkeypatch.setattr(gh, "_cache", {})
    S.reset_services()
    assert S._build("radio") is a
    assert gh._get("radio") is a
    assert R._radio() is a
    assert len(created) == 1                                    # nog steeds precies een VLC-instantie


def test_shared_player_is_created_once_under_concurrency(monkeypatch):
    import threading

    import sound_system.radio as radio_mod

    created = []

    class Slow:
        def __init__(self):
            time.sleep(0.05)                                    # traag genoeg voor een race
            created.append(1)

    monkeypatch.setattr(radio_mod, "_shared", None)
    monkeypatch.setattr(radio_mod, "RadioPlayer", Slow)
    got = []
    threads = [threading.Thread(target=lambda: got.append(radio_mod.shared_player())) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(created) == 1 and len({id(g) for g in got}) == 1


def test_concurrent_play_calls_are_serialised():
    """play() duurt tot ~4,5 s; twee gelijktijdige aanroepen (wekker + klik) mengden stop()/set_media()/play()."""
    import threading

    p = _player([vlc.State.Playing])
    inside = {"n": 0, "max": 0}
    real_set_media = p.player.set_media

    def slow_set_media(m):
        inside["n"] += 1
        inside["max"] = max(inside["max"], inside["n"])
        time.sleep(0.1)
        real_set_media(m)
        inside["n"] -= 1

    p.player.set_media = slow_set_media
    threads = [threading.Thread(target=lambda: p.play("radio538")) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert inside["max"] == 1
