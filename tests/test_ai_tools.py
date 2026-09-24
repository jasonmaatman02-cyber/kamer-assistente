"""AI-tools melden de echte uitkomst: geen 'Muziek gestart' als Spotify faalde."""
import pytest

import logic.gpt_handler as gh


class FakeDJ:
    def __init__(self, ok=True, error=None):
        self.ok = ok
        self.last_error = error
        self.calls = []

    def _res(self, name, *a):
        self.calls.append((name, a))
        if not self.ok:
            self.last_error = "Geen actief Spotify-apparaat."
        return self.ok

    def speel_muziek(self, term):
        return self._res("speel", term)

    def stop(self):
        return self._res("stop")

    def pauze(self):
        return self._res("pauze")

    def resume(self):
        return self._res("resume")

    def set_volume(self, v):
        return self._res("volume", v)

    def current_track(self):
        return {"type": "spotify"}

    class sp:
        @staticmethod
        def current_playback():
            return None                       # niets speelt: was een AttributeError op .get


def _install(monkeypatch, dj):
    monkeypatch.setattr(gh, "_get", lambda name: dj if name == "spotify" else pytest.fail(f"onverwacht {name}"))


def test_speel_muziek_reports_failure(monkeypatch):
    _install(monkeypatch, FakeDJ(ok=False))
    out = gh.speel_muziek("abba")
    assert "niet afspelen" in out and "Geen actief Spotify-apparaat" in out
    assert "gestart" not in out.lower()


def test_speel_muziek_reports_success(monkeypatch):
    _install(monkeypatch, FakeDJ(ok=True))
    assert gh.speel_muziek("abba") == "Muziek gestart: abba"


@pytest.mark.parametrize("fn,ok_text,fail_text", [
    (gh.stop_audio, "Muziek gestopt", "niet stoppen"),
    (gh.pauze_audio, "Muziek gepauzeerd", "niet pauzeren"),
])
def test_stop_and_pause_report_the_real_outcome(monkeypatch, fn, ok_text, fail_text):
    _install(monkeypatch, FakeDJ(ok=True))
    assert fn() == ok_text
    _install(monkeypatch, FakeDJ(ok=False))
    out = fn()
    assert fail_text in out and "Geen actief Spotify-apparaat" in out


def test_resume_reports_failure(monkeypatch):
    monkeypatch.setattr(gh, "_audio_active", lambda: None)
    _install(monkeypatch, FakeDJ(ok=False))
    assert "niet hervatten" in gh.resume_audio()
    _install(monkeypatch, FakeDJ(ok=True))
    assert gh.resume_audio() == "Muziek hervat"


def test_volume_reports_failure_and_survives_nothing_playing(monkeypatch):
    monkeypatch.setattr(gh, "_audio_active", lambda: "spotify")
    dj = FakeDJ(ok=False)
    _install(monkeypatch, dj)
    out = gh.pas_volume_aan("harder")
    assert "niet aanpassen" in out
    assert dj.calls == [("volume", (60,))]          # niets speelt -> vertrekpunt 50, +10
    _install(monkeypatch, FakeDJ(ok=True))
    assert gh.pas_volume_aan("zachter") == "Geluid zachter gezet"


def test_radio_failure_is_not_logged_as_started(monkeypatch):
    logged = []
    monkeypatch.setattr(gh, "log", lambda kind, msg: logged.append(msg))

    class Radio:
        last_error = "Radio kon niet starten (check URL of internet)."

        def play(self, z):
            return self.last_error

    monkeypatch.setattr(gh, "_get", lambda name: Radio())
    assert "niet starten" in gh.speel_radio("radio538")
    assert not any("gestart:" in m for m in logged) and any("mislukt" in m for m in logged)


@pytest.mark.parametrize("raw,expected", [(0, 1), (250, 100), ("70", 70), (-5, 1)])
def test_zet_lamp_clamps_brightness(monkeypatch, raw, expected):
    seen = []

    class Lamp:
        async def zet_helderheid(self, h):
            seen.append(h)

    monkeypatch.setattr(gh, "_lamp", lambda loc: Lamp())
    gh.zet_lamp(helderheid=raw)
    assert seen == [expected]


def test_zet_lamp_rejects_garbage_brightness(monkeypatch):
    monkeypatch.setattr(gh, "_lamp", lambda loc: pytest.fail("lamp mag niet aangeraakt worden"))
    assert "helderheid" in gh.zet_lamp(helderheid="fel").lower()


def test_dj_search_failures_set_last_error(monkeypatch):
    from sound_system.muziek import SpotifyDJ

    dj = object.__new__(SpotifyDJ)
    dj.last_error = None

    class SP:
        def search(self, **kw):
            return {"tracks": {"items": []}}

    dj.sp = SP()
    assert dj.speel_muziek("xyzzy") is False
    assert "Geen resultaat" in dj.last_error

    class Boom:
        def search(self, **kw):
            raise RuntimeError("netwerk weg")

    dj.sp = Boom()
    assert dj.speel_muziek("abba") is False
    assert "onbereikbaar" in dj.last_error
