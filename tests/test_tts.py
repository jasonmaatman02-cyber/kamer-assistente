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
