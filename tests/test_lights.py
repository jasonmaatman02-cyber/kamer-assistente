"""Lamp-kleuren: de automatiek kiest nooit paars ("geen paarse verlichting")."""
import asyncio

from devices.Lights import PARTY_KLEUREN, SlimmeLamp, kleuren


class _RecordingLamp:
    def __init__(self):
        self.hues = []
        self.brightness = []

    async def get_device_info(self):
        class Info:
            brightness = 40
            hue = 200
            saturation = 50

        return Info()

    async def set_brightness(self, b):
        self.brightness.append(b)

    async def set_hue_saturation(self, hue, sat):
        self.hues.append((hue, sat))


def test_party_mode_never_picks_purple(monkeypatch):
    import itertools

    import devices.Lights as L

    # Deterministisch: loop de hele kleurenlijst rond, zodat elke kandidaat voorbijkomt.
    cycle = {}
    monkeypatch.setattr(L.random, "choice",
                        lambda seq: next(cycle.setdefault(id(seq), itertools.cycle(list(seq)))))
    lamp = SlimmeLamp("u", "p", "192.0.2.1")
    lamp.lamp = _RecordingLamp()
    asyncio.run(lamp.party(duur=0.4, interval=0.001))

    picked = lamp.lamp.hues[:-1]                         # laatste = herstel van de vorige stand
    assert len(picked) >= len(PARTY_KLEUREN)             # de hele palet is minstens een keer langsgekomen
    assert kleuren["paars"] not in picked
    assert set(picked) == set(PARTY_KLEUREN)
    assert lamp.lamp.hues[-1] == (200, 50)               # vorige stand hersteld
    assert lamp.lamp.brightness[-1] == 40


def test_explicit_purple_request_still_works():
    lamp = SlimmeLamp("u", "p", "192.0.2.1")
    lamp.lamp = _RecordingLamp()
    assert "paars" in asyncio.run(lamp.zet_kleur("paars"))
    assert lamp.lamp.hues == [kleuren["paars"]]
