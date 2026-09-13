"""Wake-word listening loop.

Records a short chunk, transcribes it locally (:mod:`ai.stt`, faster-whisper by
default), and if it matches a wake word records a command, transcribes that and
hands it to :func:`logic.gpt_handler.verwerk_input`.
"""
from __future__ import annotations

import difflib
import re
import string
import time

import config
from ai import stt
from logic.gpt_handler import verwerk_input
from logic.logger import log
from voice.tts_output import speak

# sounddevice/numpy zijn alleen in de volledige voice-stack (requirements.txt),
# niet in een dashboard-only install (requirements-dashboard.txt). Een missende
# of kapotte PortAudio-lib mag dit module NIET onimporteerbaar maken — anders
# valt alles wat 'm transitief importeert (o.a. scheduler.routines, en dus de
# hele wekker/routine-functionaliteit in het dashboard) stil mee om.
try:
    import numpy as np
except Exception as exc:  # noqa: BLE001
    np = None
    print(f"[AUDIO] numpy niet beschikbaar, spraakinvoer uitgeschakeld: {exc}")
try:
    import sounddevice as sd
except Exception as exc:  # noqa: BLE001
    sd = None
    print(f"[AUDIO] sounddevice niet beschikbaar, spraakinvoer uitgeschakeld: {exc}")

try:
    from voice.porcupine_wake import detect_wakeword_porcupine, porcupine_ready
except Exception:  # noqa: BLE001 - pvporcupine/sounddevice ontbreekt
    detect_wakeword_porcupine = None

    def porcupine_ready() -> bool:
        return False


SAMPLERATE = 16000
WAKE_DURATION = 2.0
COMMAND_DURATION = 5.0
PORCUPINE_LISTEN_TIMEOUT = 20.0

_wake_backend_warned = False


def _use_porcupine() -> bool:
    """Kies Porcupine als de config 't toestaat en alles aanwezig is."""
    global _wake_backend_warned
    mode = config.get("assistant.wake_backend", "auto")
    if mode == "whisper" or detect_wakeword_porcupine is None:
        return False
    if porcupine_ready():
        return True
    if mode == "porcupine" and not _wake_backend_warned:
        log("Error", "wake_backend=porcupine maar niet klaar (key/.ppn?) — val terug op whisper")
        _wake_backend_warned = True
    return False


class NoMicError(RuntimeError):
    pass


_record_rate_cache: int | None = None


def _pick_record_rate() -> int:
    """Kies een samplerate die de mic ECHT ondersteunt. SAMPLERATE (16 kHz) is
    wat de rest van de pijplijn (faster-whisper) verwacht, maar sommige
    USB-webcam-microfoons ondersteunen alleen een vaste set (bv. 8/32/44.1/48
    kHz — mist dan juist 16 kHz). In dat geval nemen we de eigen default-rate
    van het apparaat op en resamplen we terug naar SAMPLERATE (zie _record).
    Gooit door als er helemaal geen bruikbare invoer-rate is."""
    global _record_rate_cache
    if _record_rate_cache is not None:
        return _record_rate_cache
    try:
        sd.check_input_settings(samplerate=SAMPLERATE, channels=1)
        _record_rate_cache = SAMPLERATE
        return _record_rate_cache
    except Exception:  # noqa: BLE001
        pass
    default_rate = int(sd.query_devices(kind="input")["default_samplerate"])
    sd.check_input_settings(samplerate=default_rate, channels=1)  # gooit door als dit ook niet lukt
    print(f"[AUDIO] mic ondersteunt {SAMPLERATE}Hz niet, gebruik native {default_rate}Hz + resample")
    _record_rate_cache = default_rate
    return _record_rate_cache


def _resample(audio, orig_rate: int, target_rate: int):
    """Lichte, dependency-vrije lineaire resample — ruim voldoende voor
    spraak/wake-word-herkenning, geen extra scipy-afhankelijkheid nodig."""
    if orig_rate == target_rate or audio.size == 0:
        return audio
    target_len = max(1, int(round(audio.shape[0] * target_rate / orig_rate)))
    orig_idx = np.arange(audio.shape[0])
    target_idx = np.linspace(0, audio.shape[0] - 1, num=target_len)
    return np.interp(target_idx, orig_idx, audio).astype(audio.dtype)


def mic_available() -> bool:
    if sd is None:
        return False
    try:
        _pick_record_rate()
        return True
    except Exception:  # noqa: BLE001
        return False


def _record(seconds: float):
    if sd is None or np is None:
        raise NoMicError("sounddevice/numpy niet geïnstalleerd")
    try:
        rate = _pick_record_rate()
        audio = sd.rec(int(rate * seconds), samplerate=rate, channels=1, dtype="float32")
        sd.wait()
    except Exception as exc:  # noqa: BLE001 - sounddevice/PortAudio errors
        raise NoMicError(str(exc)) from exc
    audio = np.squeeze(audio)
    if rate != SAMPLERATE:
        audio = _resample(audio, rate, SAMPLERATE)
    peak = np.max(np.abs(audio)) if audio.size else 0
    return audio / peak if peak > 0 else audio


def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(f"[{re.escape(string.punctuation)}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_wake(text: str, threshold: float = 0.6) -> bool:
    t = _normalise(text)
    if not t:
        return False
    words = [_normalise(w) for w in config.get("assistant.wake_words", ["hey kamer"])]
    if any(w and w in t for w in words):
        return True
    return max((difflib.SequenceMatcher(None, t, w).ratio() for w in words), default=0) >= threshold


def whisperrr():
    if not config.get("features.voice_assistant", True):
        time.sleep(5)  # uitgezet via Settings — rustig blijven pollen
        return
    try:
        if _use_porcupine():
            try:
                if not detect_wakeword_porcupine(timeout=PORCUPINE_LISTEN_TIMEOUT):
                    return
            except Exception as exc:  # noqa: BLE001 - val terug op whisper deze ronde
                log("Error", f"Porcupine-fout, whisper-fallback: {exc}")
                if not _is_wake(stt.transcribe_array(_record(WAKE_DURATION), SAMPLERATE)):
                    return
        else:
            wake_text = stt.transcribe_array(_record(WAKE_DURATION), SAMPLERATE)
            if wake_text:
                print(f"Gehoord: {wake_text}")
            if not _is_wake(wake_text):
                return

        print("Wake word herkend, neem opdracht op...")
        speak("Zeg het eens.")
        log("Input", "Wake word herkend")

        command = stt.transcribe_array(_record(COMMAND_DURATION), SAMPLERATE)
        if not command:
            print("Kon opdracht niet verstaan.")
            log("Error", "Kon opdracht niet transcriberen")
            return

        print("Jij zei:", command)
        antwoord = verwerk_input(command)
        if antwoord:
            speak(antwoord)
    except NoMicError as exc:
        log("Error", f"Geen microfoon: {exc}")
        print(f"Geen microfoon beschikbaar ({exc}) — 30s pauze.")
        time.sleep(30)
    except Exception as exc:  # noqa: BLE001
        print(f"Er ging iets mis: {exc}")
        log("Error", f"Fout in wake-loop: {exc}")
        time.sleep(2)
