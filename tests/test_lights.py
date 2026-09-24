"""Lamp-kleuren: de automatiek kiest nooit paars ("geen paarse verlichting")."""
import asyncio

import pytest

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


def test_connect_without_tapo_account_fails_clearly_without_touching_the_network(monkeypatch):
    import devices.Lights as L

    def boom(*a, **kw):
        raise AssertionError("er mag geen ApiClient/netwerkpoging zijn zonder account")

    monkeypatch.setattr(L, "ApiClient", boom)
    for creds in (("", ""), (None, None), ("a@b.c", ""), ("", "pw")):
        with pytest.raises(RuntimeError, match="Tapo-account niet ingesteld"):
            asyncio.run(SlimmeLamp(*creds, "192.0.2.1").connect())


def test_missing_tapo_account_is_a_clear_message_at_the_api(client, monkeypatch):
    import config

    config.set("devices.lamps", [{"name": "A", "ip": "192.0.2.1"}])
    r = client.put("/api/lamp/on", json={"lamp": 0})
    assert r.status_code == 500
    assert "Tapo-account niet ingesteld" in r.get_json()["message"]


def test_hex_accepts_the_short_form_and_rejects_garbage():
    from devices.Lights import hex_to_hue_saturation

    assert hex_to_hue_saturation("#f00") == hex_to_hue_saturation("#ff0000") == (0, 100)
    assert hex_to_hue_saturation("  #0F0 ") == (120, 100)
    for bad in ("#zzzzzz", "#12", "#1234567", "", "#"):
        with pytest.raises(ValueError):
            hex_to_hue_saturation(bad)


def test_zet_kleur_with_a_bad_hex_is_a_message_not_an_exception():
    import asyncio

    from devices.Lights import SlimmeLamp

    lamp = SlimmeLamp("u", "p", "192.0.2.1")
    lamp.lamp = object()                                  # mag niet aangeraakt worden
    out = asyncio.run(lamp.zet_kleur("#nietgeldig"))
    assert "ongeldig" in out
