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
