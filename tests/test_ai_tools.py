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


def test_zet_lamp_without_lamps_gives_a_clear_message():
    import config

    config.set("devices.lamps", [])
    out = gh._dispatch({"name": "zet_lamp", "arguments": {"aan": True}})
    assert "Geen lamp geconfigureerd" in out


def test_lamp_lookup_by_name_and_fallback(monkeypatch):
    import config
    import devices.Lights as L

    seen = []

    class FakeLamp:
        def __init__(self, user, pw, ip):
            seen.append(ip)

        async def connect(self):
            pass

    monkeypatch.setattr(L, "SlimmeLamp", FakeLamp)
    config.set("devices.lamps", [{"name": "Bureaulamp", "ip": "192.0.2.1"}, {"name": "Slaapkamer", "ip": "192.0.2.2"}, {"name": "Kapot"}])
    gh._lamp("slaap")
    gh._lamp("woonkamer")           # onbekend: eerste lamp, zoals voorheen
    assert seen == ["192.0.2.2", "192.0.2.1"]


# --------------------------------------------------------------------------- #
# Argumenten van het model: verzonnen parameters, geen object, enorme uitkomst
# --------------------------------------------------------------------------- #
def test_hallucinated_tool_parameters_are_dropped_not_fatal(monkeypatch):
    """Logboek 2026-09-14: 'verzend_logs_per_mail() got an unexpected keyword argument prompt'."""
    seen = {}
    monkeypatch.setitem(gh.functies_dispatcher, "verzend_logs_per_mail",
                        lambda: seen.setdefault("called", True) and "Logs verzonden")
    out = gh._dispatch({"name": "verzend_logs_per_mail", "arguments": {"prompt": "stuur ze maar"}})
    assert out == "Logs verzonden" and seen == {"called": True}


def test_known_parameters_still_pass_and_extra_ones_are_dropped(monkeypatch):
    got = {}

    def zet(kleur, helderheid=100):
        got.update(kleur=kleur, helderheid=helderheid)
        return "ok"

    monkeypatch.setitem(gh.functies_dispatcher, "zet_x", zet)
    assert gh._dispatch({"name": "zet_x", "arguments": {"kleur": "rood", "helderheid": 30, "snelheid": 9}}) == "ok"
    assert got == {"kleur": "rood", "helderheid": 30}


def test_a_missing_required_parameter_is_still_a_clear_error(monkeypatch):
    monkeypatch.setitem(gh.functies_dispatcher, "zet_x", lambda kleur: "ok")
    out = gh._dispatch({"name": "zet_x", "arguments": {"verzonnen": 1}})
    assert out.startswith("(kon 'zet_x' niet uitvoeren") and "kleur" in out


def test_var_keyword_tools_get_everything(monkeypatch):
    monkeypatch.setitem(gh.functies_dispatcher, "flex", lambda **kw: ",".join(sorted(kw)))
    assert gh._dispatch({"name": "flex", "arguments": {"a": 1, "b": 2}}) == "a,b"


@pytest.mark.parametrize("raw", [None, [], ["a"], "tekst", 5, [1, 2]])
def test_non_object_arguments_are_treated_as_none(monkeypatch, raw):
    monkeypatch.setitem(gh.functies_dispatcher, "geen_args", lambda: "gedaan")
    assert gh._dispatch({"name": "geen_args", "arguments": raw}) == "gedaan"


def test_huge_tool_results_are_truncated(monkeypatch):
    monkeypatch.setitem(gh.functies_dispatcher, "groot", lambda: "x" * 50_000)
    out = gh._dispatch({"name": "groot", "arguments": {}})
    assert len(out) < gh._MAX_TOOL_RESULT_CHARS + 40 and out.endswith("(ingekort)")
    monkeypatch.setitem(gh.functies_dispatcher, "klein", lambda: "kort")
    assert gh._dispatch({"name": "klein", "arguments": {}}) == "kort"
