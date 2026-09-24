"""Spraakpijplijn: microfoon weg/hangend, STT-fouten, geen ongevraagde cloud-
uploads van kamergeluid, geen log-spam bij een blijvende fout."""
import threading
import time

import numpy as np
import pytest

import config
from ai import stt
from voice import Whisper


# --------------------------------------------------------------------------- #
# hulpjes
# --------------------------------------------------------------------------- #
class _Stream:
    def __init__(self, active_for):
        self._until = time.monotonic() + active_for

    @property
    def active(self):
        return time.monotonic() < self._until


class FakeSD:
    """Minimale sounddevice: opname duurt ``hang_s`` seconden (default: meteen klaar)."""

    def __init__(self, hang_s=0.0, rec_error=None):
        self.hang_s = hang_s
        self.rec_error = rec_error
        self.stopped = 0
        self.terminated = 0
        self.initialized = 0
        self._stream = None

    def check_input_settings(self, samplerate, channels):
        pass

    def rec(self, n, samplerate, channels, dtype):
        if self.rec_error:
            raise self.rec_error
        self._stream = _Stream(self.hang_s)
        return np.full((n, channels), 0.5, dtype=dtype)

    def get_stream(self):
        if self._stream is None:
            raise RuntimeError("no stream")
        return self._stream

    def wait(self):
        pass

    def stop(self):
        self.stopped += 1
        self._stream = _Stream(0)

    def _terminate(self):
        self.terminated += 1

    def _initialize(self):
        self.initialized += 1


@pytest.fixture()
def fake_sd(monkeypatch):
    def install(**kw):
        fake = FakeSD(**kw)
        monkeypatch.setattr(Whisper, "sd", fake)
        monkeypatch.setattr(Whisper, "_record_rate_cache", None)
        return fake

    monkeypatch.setattr(Whisper, "_loop_errors", 0)
    monkeypatch.setattr(Whisper, "_loop_last_logged", {})
    return install


# --------------------------------------------------------------------------- #
# opname
# --------------------------------------------------------------------------- #
def test_record_gives_up_when_the_capture_never_finishes(fake_sd, monkeypatch):
    """sd.wait() heeft geen timeout: een USB-mic die tijdens de opname verdwijnt
    liet de hele luisterlus (en de bedtijd-routine-thread) voor altijd hangen."""
    fake = fake_sd(hang_s=60)
    monkeypatch.setattr(Whisper, "_RECORD_SLACK_S", 0.3)
    t0 = time.monotonic()
    with pytest.raises(Whisper.NoMicError, match="hing"):
        Whisper._record(0.2)
    assert time.monotonic() - t0 < 3
    assert fake.stopped == 1


def test_record_returns_normally_when_the_capture_finishes(fake_sd):
    fake_sd(hang_s=0.2)
    audio = Whisper._record(0.2)
    assert audio.shape[0] == int(Whisper.SAMPLERATE * 0.2)


def test_record_maps_portaudio_errors_to_nomicerror(fake_sd):
    fake_sd(rec_error=OSError("Error querying device -1"))
    with pytest.raises(Whisper.NoMicError):
        Whisper._record(0.1)


# --------------------------------------------------------------------------- #
# herstel na mic-verlies + luisterlus
# --------------------------------------------------------------------------- #
def test_lost_mic_reinitialises_portaudio_and_forgets_the_cached_rate(fake_sd, monkeypatch):
    """PortAudio leest de apparaatlijst één keer; zonder herinitialisatie blijft een
    opnieuw aangesloten USB-mic tot een herstart van het proces onvindbaar, en
    een gecachete samplerate van het oude apparaat blijft hangen."""
    fake = fake_sd(rec_error=OSError("weg"))
    monkeypatch.setattr(Whisper, "_record_rate_cache", 44100)
    slept = []
    monkeypatch.setattr(Whisper.time, "sleep", slept.append)

    Whisper.whisperrr()

    assert fake.terminated == 1 and fake.initialized == 1
    assert Whisper._record_rate_cache is None
    assert slept == [30]


def test_recover_audio_initialises_even_if_terminate_fails(fake_sd):
    fake = fake_sd()

    def boom():
        raise RuntimeError("PortAudio not initialized")

    fake._terminate = boom
    Whisper._recover_audio()
    assert fake.initialized == 1


def test_persistent_mic_error_is_logged_once_not_every_round(fake_sd, monkeypatch):
    fake_sd(rec_error=OSError("Error querying device -1"))
    logged = []
    monkeypatch.setattr(Whisper, "log", lambda kind, msg: logged.append((kind, msg)))
    monkeypatch.setattr(Whisper.time, "sleep", lambda s: None)

    for _ in range(20):
        Whisper.whisperrr()

    assert len([m for k, m in logged if "microfoon" in m.lower()]) == 1, logged


def test_error_is_logged_again_after_the_dedup_window(fake_sd, monkeypatch):
    fake_sd(rec_error=OSError("weg"))
    logged = []
    real_sleep = time.sleep                      # Whisper.time IS de time-module: patch raakt ook onze eigen sleep
    monkeypatch.setattr(Whisper, "log", lambda kind, msg: logged.append(msg))
    monkeypatch.setattr(Whisper.time, "sleep", lambda s: None)
    monkeypatch.setattr(Whisper, "_LOOP_ERROR_LOG_EVERY_S", 0.05)

    Whisper.whisperrr()
    real_sleep(0.1)
    Whisper.whisperrr()
    assert len(logged) == 2


def test_generic_loop_errors_back_off_and_a_quiet_round_resets(fake_sd, monkeypatch):
    fake_sd()
    slept = []
    monkeypatch.setattr(Whisper.time, "sleep", slept.append)
    monkeypatch.setattr(Whisper, "log", lambda *a: None)
    outcome = {"boom": True}

    def once():
        if outcome["boom"]:
            raise ValueError("kapot")

    monkeypatch.setattr(Whisper, "_listen_once", once)

    for _ in range(6):
        Whisper.whisperrr()
    assert slept == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]

    outcome["boom"] = False
    Whisper.whisperrr()                       # ronde zonder fout (bv. geen wake-woord)
    outcome["boom"] = True
    slept.clear()
    Whisper.whisperrr()
    assert slept == [2.0]                     # teller is teruggezet


def test_wake_chunks_never_use_the_cloud_fallback(fake_sd, monkeypatch):
    fake_sd()
    seen = []

    def fake_transcribe_array(samples, rate, language=None, cloud_fallback=True):
        seen.append(cloud_fallback)
        return ""

    monkeypatch.setattr(Whisper.stt, "transcribe_array", fake_transcribe_array)
    monkeypatch.setattr(Whisper, "_use_porcupine", lambda: False)
    Whisper._listen_once()
    assert seen == [False]


# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #
@pytest.fixture()
def no_local_models(monkeypatch):
    def broken(path, language):
        raise RuntimeError("model niet beschikbaar")

    monkeypatch.setattr(stt, "_tr_faster_whisper", broken)
    monkeypatch.setattr(stt, "_tr_whisper", broken)
    stt._load_failed.clear()


@pytest.fixture()
def cloud_calls(monkeypatch):
    import requests

    calls = []

    def post(*a, **kw):
        calls.append((a, kw))
        raise AssertionError("er mag geen request naar OpenAI gaan")

    monkeypatch.setattr(requests, "post", post)
    return calls


def test_openai_fallback_without_key_makes_no_request(tmp_path, no_local_models, cloud_calls):
    """Zonder key stuurde _tr_openai toch de audio (met 'Bearer ') naar OpenAI, elke
    ~2s in de wake-lus, alleen om een 401 terug te krijgen."""
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    config.set("stt.backend", "faster_whisper")
    assert stt.transcribe(str(wav)) == ""
    assert cloud_calls == []


def test_wake_transcription_does_not_upload_room_audio_when_local_stt_fails(
        tmp_path, monkeypatch, no_local_models):
    """Met een OPENAI_API_KEY (voor de chat) en een kapot lokaal model stuurde de
    wake-lus 24/7 kamergeluid naar de betaalde cloud-API (~$8/dag)."""
    import requests

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    posted = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: posted.append(1) or (_ for _ in ()).throw(AssertionError))
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    config.set("stt.backend", "faster_whisper")

    assert stt.transcribe(str(wav), cloud_fallback=False) == ""
    assert posted == []

    # een bewust gevraagde opdracht (cloud_fallback default) mag wél terugvallen
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "zet de lamp aan"}

    monkeypatch.setattr(requests, "post", lambda *a, **kw: posted.append(1) or Resp())
    assert stt.transcribe(str(wav)) == "zet de lamp aan"
    assert posted == [1]


def test_explicit_openai_backend_still_works_without_cloud_fallback_flag(tmp_path, monkeypatch, no_local_models):
    import requests

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    config.set("stt.backend", "openai")

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "hallo"}

    monkeypatch.setattr(requests, "post", lambda *a, **kw: Resp())
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    assert stt.transcribe(str(wav), cloud_fallback=False) == "hallo"


def _install_fake_faster_whisper(monkeypatch, load_s=0.0, fail=False):
    import sys
    import types

    loads = []

    class WhisperModel:
        def __init__(self, *a, **kw):
            loads.append(1)
            time.sleep(load_s)
            if fail:
                raise OSError("geen internet, model niet gecached")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(stt, "_fw_model", None)
    monkeypatch.setattr(stt, "_fw_key", None)
    stt._load_failed.clear()
    return loads


def test_concurrent_transcriptions_load_the_model_once(monkeypatch):
    loads = _install_fake_faster_whisper(monkeypatch, load_s=0.3)
    threads = [threading.Thread(target=stt._get_fw) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(loads) == 1


def test_failing_model_load_is_not_retried_every_round(monkeypatch):
    """Geen internet + model niet gecached: elke ~2s (wake-lus) opnieuw een
    laadpoging (netwerk-timeouts, CPU). Nu: één poging per minuut."""
    loads = _install_fake_faster_whisper(monkeypatch, fail=True)
    for _ in range(5):
        with pytest.raises(Exception):
            stt._get_fw()
    assert len(loads) == 1

    monkeypatch.setattr(stt, "_LOAD_RETRY_S", 0.0)
    stt._load_failed.clear()
    with pytest.raises(OSError):
        stt._get_fw()
    assert len(loads) == 2
