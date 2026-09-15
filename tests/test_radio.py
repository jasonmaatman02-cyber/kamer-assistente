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
