"""Short one-shot listen used by the bedtime routine (wait for wake word, then
record a single answer and return its transcription)."""
from __future__ import annotations

import time

import config
from ai import stt
from voice.Whisper import _is_wake, _record, NoMicError, SAMPLERATE

COMMAND_DURATION = 5.0

try:
    from voice.porcupine_wake import detect_wakeword_porcupine
except Exception:  # noqa: BLE001
    detect_wakeword_porcupine = None


def wacht_op_wakeword(timeout: float | None = 30) -> bool:
    start = time.time()
    while timeout is None or (time.time() - start) < timeout:
        try:
            text = stt.transcribe_array(_record(1.5), SAMPLERATE)
            if text:
                print(f"Herkend (kort): {text}")
                if _is_wake(text):
                    return True
        except NoMicError as exc:
            print(f"Geen microfoon: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            print(f"Luisterfout: {exc}")
            time.sleep(0.5)
    return False


def shortwhisper() -> str | None:
    found = False
    if detect_wakeword_porcupine is not None:
        try:
            found = detect_wakeword_porcupine(timeout=30)
        except Exception as exc:  # noqa: BLE001
            print(f"Porcupine niet beschikbaar: {exc}")
            found = wacht_op_wakeword(timeout=30)
    else:
        found = wacht_op_wakeword(timeout=30)

    if not found:
        print("Geen wakeword binnen timeout")
        return None

    return stt.transcribe_array(_record(COMMAND_DURATION), SAMPLERATE) or None
