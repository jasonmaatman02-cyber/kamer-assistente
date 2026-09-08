"""Porcupine wakeword detector helper.

Usage:
 - Install pvporcupine: `pip install pvporcupine` (Picovoice licensing applies)
 - Generate a custom keyword (.ppn) at https://console.picovoice.ai/ if you need Dutch
 - Place the .ppn file in the `voice/` folder or provide the path when calling

Functions:
 - `detect_wakeword_porcupine(keyword_paths=None, sensitivity=0.5, timeout=30)`
    listens on the default input device and returns True on detection.

The implementation uses blocking reads from `sounddevice.RawInputStream` and
is intentionally simple so it can be called from scripts.
"""
from __future__ import annotations

import os
import time
import numpy as np
import sounddevice as sd

try:
    import pvporcupine
except Exception:  # pragma: no cover - graceful fallback when library missing
    pvporcupine = None


def detect_wakeword_porcupine(keyword_paths: list[str] | None = None, sensitivity: float = 0.5, timeout: float = 30) -> bool:
    """Return True if Porcupine detects the provided keyword within `timeout` seconds.

    - `keyword_paths`: list of full paths to .ppn keyword files. If None, will try
      to use `voice/hey_kamer.ppn` in the same folder as this file.
    - `sensitivity`: 0..1 sensitivity for detection (higher = more sensitive)
    - `timeout`: seconds to wait before giving up
    """
    if pvporcupine is None:
        raise ImportError("pvporcupine is not installed")

    if keyword_paths is None:
        default = os.path.join(os.path.dirname(__file__), "hey_kamer.ppn")
        if os.path.exists(default):
            keyword_paths = [default]
        else:
            raise ValueError("No keyword_paths provided and default keyword file not found: %s" % default)

    porcupine = pvporcupine.create(keyword_paths=keyword_paths, sensitivities=[sensitivity] * len(keyword_paths))
    try:
        frame_length = porcupine.frame_length
        sample_rate = porcupine.sample_rate

        with sd.RawInputStream(samplerate=sample_rate, blocksize=frame_length, dtype='int16', channels=1) as stream:
            start = time.time()
            while True:
                if timeout and (time.time() - start) > timeout:
                    return False
                pcm = stream.read(frame_length)[0]
                if not pcm:
                    continue
                pcm = np.frombuffer(pcm, dtype=np.int16)
                try:
                    result = porcupine.process(pcm)
                except Exception:
                    continue
                if result >= 0:
                    return True
    finally:
        try:
            porcupine.delete()
        except Exception:
            pass
