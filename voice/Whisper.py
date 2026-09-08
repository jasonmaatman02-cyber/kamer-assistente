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

import numpy as np
import sounddevice as sd

import config
from ai import stt
from logic.gpt_handler import verwerk_input
from logic.logger import log
from voice.tts_output import speak

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


def mic_available() -> bool:
    try:
        sd.check_input_settings(samplerate=SAMPLERATE, channels=1)
        return True
    except Exception:  # noqa: BLE001
        return False


def _record(seconds: float) -> np.ndarray:
    try:
        audio = sd.rec(int(SAMPLERATE * seconds), samplerate=SAMPLERATE, channels=1, dtype="float32")
        sd.wait()
    except Exception as exc:  # noqa: BLE001 - sounddevice/PortAudio errors
        raise NoMicError(str(exc)) from exc
    audio = np.squeeze(audio)
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
