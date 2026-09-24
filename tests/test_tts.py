"""ai/tts.py: subprocess-aanroepen (espeak-ng, systeemspelers) mogen nooit
voor altijd kunnen hangen -- ontbrekende timeouts waren hier een echte bug
(een hangend extern proces blokkeerde de aanroepende thread, bv. de
spraaklus of een routine-callback, voorgoed)."""
import subprocess


def test_synth_espeak_passes_timeout(monkeypatch):
    import ai.tts as tts

    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts._synth_espeak("hallo", "/tmp/out.wav")
    assert seen.get("timeout") is not None and seen["timeout"] > 0


def test_speak_espeak_passes_timeout(monkeypatch):
    import ai.tts as tts

    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts._speak_espeak("hallo")
    assert seen.get("timeout") is not None and seen["timeout"] > 0


def test_play_system_passes_timeout_to_every_player(monkeypatch):
    import ai.tts as tts

    seen = []

    def fake_run(cmd, **kwargs):
        seen.append((cmd[0], kwargs.get("timeout")))
        raise FileNotFoundError("player niet aanwezig")   # simuleert alle spelers ontbreken

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts._play_system("/tmp/out.wav")   # mag niet crashen, gewoon door de lijst heen
    assert len(seen) == 3   # aplay, ffplay, afplay geprobeerd
    assert all(timeout for _player, timeout in seen)


def test_play_system_recovers_from_hung_player(monkeypatch):
    """Een speler die 'hangt' (TimeoutExpired) mag de volgende speler in de
    lijst niet blokkeren -- exact het scenario dat de ontbrekende timeout
    eerder onmogelijk maakte om uit te herstellen."""
    import ai.tts as tts

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[0])
        if cmd[0] == "aplay":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0)   # ffplay lukt wel

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts._play_system("/tmp/out.wav")
    assert calls == ["aplay", "ffplay"]   # gestopt zodra ffplay lukte, geen afplay meer nodig


def test_speak_with_espeak_backend_does_not_hang_on_timeout(monkeypatch):
    """speak() met tts.backend=espeak mag nooit een onopgevangen
    TimeoutExpired laten ontsnappen naar de aanroeper (spraaklus/routine)."""
    import config
    import ai.tts as tts

    config.set("tts.backend", "espeak")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts.speak("dit hangt normaal")   # mag geen exception laten ontsnappen


# --------------------------------------------------------------------------- #
# Sessie 2: argument-injectie, gelijktijdig spreken, afspeel-timeout, temp-lek
# --------------------------------------------------------------------------- #
def test_espeak_text_can_never_be_parsed_as_an_option(monkeypatch):
    """LLM-antwoorden, notities en routine-stappen komen als losse argv naar
    espeak-ng; tekst die met '-' begint (bv. '-w/pad') werd als optie gelezen.
    '--' vlak voor de tekst voorkomt dat."""
    import ai.tts as tts

    cmds = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: cmds.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    tts._synth_espeak("-w/home/pi/.bashrc", "/tmp/out.wav")
    tts._speak_espeak("--stdin")

    for cmd in cmds:
        assert cmd[-2] == "--" and cmd[-1] in ("-w/home/pi/.bashrc", "--stdin"), cmd


def test_speak_is_serialized_no_overlapping_playback(monkeypatch):
    """Een wekker die afgaat tijdens een routine mag pygame.mixer.music (één
    globaal kanaal) niet dubbel gebruiken: speak() moet elkaar afwachten."""
    import threading
    import time

    import config
    import ai.tts as tts

    config.set("tts.backend", "piper")
    active = {"n": 0, "max": 0}
    guard = threading.Lock()

    def fake_synth(text, out_path=None):
        return __file__   # bestaand pad; wordt door speak() na afloop verwijderd -> zie monkeypatch os.remove

    def fake_play(path):
        with guard:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        with guard:
            active["n"] -= 1

    monkeypatch.setattr(tts, "synthesize", fake_synth)
    monkeypatch.setattr(tts, "_play", fake_play)
    monkeypatch.setattr(tts.os, "remove", lambda p: None)   # test-bestand niet echt weggooien

    threads = [threading.Thread(target=tts.speak, args=(f"zin {i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert active["max"] == 1, f"{active['max']} spraakaanroepen tegelijk actief"


def test_play_gives_up_when_audio_device_never_finishes(monkeypatch):
    """get_busy() dat voor altijd True blijft (device weg tijdens afspelen)
    mag de aanroeper niet voor eeuwig laten hangen."""
    import sys
    import types

    import ai.tts as tts

    stopped = {"n": 0}

    class Music:
        def load(self, p): pass
        def set_volume(self, v): pass
        def play(self): pass
        def get_busy(self): return True
        def stop(self): stopped["n"] += 1
        def unload(self): pass

    mixer = types.SimpleNamespace(init=lambda: None, music=Music())
    fake_pygame = types.SimpleNamespace(mixer=mixer)
    monkeypatch.setitem(sys.modules, "pygame", fake_pygame)
    monkeypatch.setattr(tts, "_mixer_ready", True)
    monkeypatch.setattr(tts, "_PLAY_MAX_S", 0.3)

    import time
    t0 = time.monotonic()
    tts._play("x.wav")
    assert time.monotonic() - t0 < 3
    assert stopped["n"] == 1


def test_failed_synthesis_does_not_leave_temp_files(monkeypatch, tmp_path):
    import tempfile

    import config
    import ai.tts as tts

    config.set("tts.backend", "piper")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(tts, "_synth_piper", lambda text, out: (_ for _ in ()).throw(RuntimeError("stem kapot")))
    monkeypatch.setattr(tts, "_synth_espeak", lambda text, out: (_ for _ in ()).throw(RuntimeError("espeak kapot")))

    assert tts.synthesize("hallo") is None
    leftovers = [f.name for f in tmp_path.iterdir() if f.suffix in (".wav", ".mp3")]
    assert leftovers == [], f"mkstemp-bestand bleef liggen na mislukte synthese: {leftovers}"


def test_piper_voice_loads_only_once_under_concurrency(monkeypatch):
    import sys
    import threading
    import time
    import types

    import config
    import ai.tts as tts

    config.set("tts.piper_model", "test-stem")
    monkeypatch.setattr(tts, "_piper_voice", None)
    monkeypatch.setattr(tts, "_piper_voice_name", None)
    loads = {"n": 0}

    class PiperVoice:
        @staticmethod
        def load(path):
            loads["n"] += 1
            time.sleep(0.1)
            return object()

    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(PiperVoice=PiperVoice))
    monkeypatch.setattr(tts, "_piper_model_path", lambda name: "x.onnx")

    barrier = threading.Barrier(5)

    def worker():
        barrier.wait(timeout=5)
        tts._get_piper_voice()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert loads["n"] == 1, f"stem {loads['n']}x geladen (elk ~60MB op een Pi)"


def _write_wav(path, seconds, rate=8000):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def test_system_player_timeout_follows_the_audio_length(tmp_path):
    """Vast 20 s kapte elke langere spraak af (en ffplay speelde 'm daarna opnieuw)."""
    import ai.tts as tts

    short, long_ = tmp_path / "kort.wav", tmp_path / "lang.wav"
    _write_wav(short, 2)
    _write_wav(long_, 55)
    assert tts._system_play_timeout(str(short)) == 20.0          # ondergrens
    assert tts._system_play_timeout(str(long_)) == 65.0          # 55 s + 10 s marge
    assert tts._system_play_timeout(str(tmp_path / "bestaat-niet.wav")) == 20.0
    assert tts._system_play_timeout(str(tmp_path / "antwoord.mp3")) == 120.0


def test_play_system_uses_the_length_based_timeout(monkeypatch, tmp_path):
    import ai.tts as tts

    wav = tmp_path / "lang.wav"
    _write_wav(wav, 50)
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts._play_system(str(wav))
    assert seen == [60.0]


def test_the_audio_length_timeout_is_capped(monkeypatch, tmp_path):
    import ai.tts as tts

    wav = tmp_path / "enorm.wav"
    _write_wav(wav, 400, rate=1000)
    assert tts._system_play_timeout(str(wav)) == float(tts._PLAY_MAX_S)


def test_missing_pygame_uses_the_system_player_quietly(monkeypatch, capsys):
    """Op de Pi (dashboard-only) staat pygame er niet: elke uitspraak schreef 'afspelen mislukt'."""
    import sys

    import ai.tts as tts

    monkeypatch.setitem(sys.modules, "pygame", None)          # import pygame -> ImportError
    played = []
    monkeypatch.setattr(tts, "_play_system", played.append)
    tts._play("/tmp/x.wav")
    assert played == ["/tmp/x.wav"]
    assert "mislukt" not in capsys.readouterr().out


def test_repeated_backend_failure_is_printed_once_not_per_utterance(monkeypatch, capsys):
    import ai.tts as tts
    import config

    config.set("tts.backend", "piper")
    monkeypatch.setattr(tts, "_printed", {})

    def no_piper(text, out):
        raise ModuleNotFoundError("No module named 'piper'")

    def fake_espeak(text, out):
        return out

    monkeypatch.setattr(tts, "_synth_piper", no_piper)
    monkeypatch.setattr(tts, "_synth_espeak", fake_espeak)
    for _ in range(5):
        path = tts.synthesize("hoi")
        assert path and path.endswith(".wav")                     # de espeak-terugval werkt gewoon
        import os
        os.remove(path)
    assert capsys.readouterr().out.count("faalde") == 1


def test_direct_espeak_timeout_scales_with_the_text(monkeypatch):
    import ai.tts as tts

    assert tts._espeak_speak_timeout("hoi") == 15.0
    assert tts._espeak_speak_timeout("x" * 500) == 70.0
    assert tts._espeak_speak_timeout("x" * 100000) == float(tts._PLAY_MAX_S)
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts._speak_espeak("y" * 500)
    assert seen["timeout"] == 70.0
