"""Porcupine wake-word detector (optioneel, veel lichter dan whisper-transcriptie).

Nodig:
 - `pip install pvporcupine`
 - Een gratis AccessKey van https://console.picovoice.ai/  -> zet als
   `PICOVOICE_ACCESS_KEY` in `.env` (of via Settings -> Inloggegevens).
 - Een keyword-bestand (`.ppn`). Maak een Nederlandse "hey kamer" aan op de
   console en leg 'm neer als `voice/hey_kamer.ppn` (pad instelbaar via
   `assistant.porcupine_keyword`).

`porcupine_ready()` zegt of dat alles klopt zonder te crashen.
`detect_wakeword_porcupine(timeout=...)` luistert en geeft True bij detectie.
"""
from __future__ import annotations

import os
import time

import numpy as np
import sounddevice as sd

import config

try:
    import pvporcupine
except Exception:  # pragma: no cover - lib niet geinstalleerd
    pvporcupine = None


def _keyword_path() -> str:
    p = config.get("assistant.porcupine_keyword", "voice/hey_kamer.ppn")
    if not os.path.isabs(p):
        # relatief t.o.v. de repo-root (map boven voice/)
        p = os.path.join(os.path.dirname(os.path.dirname(__file__)), p)
    return p


def porcupine_ready() -> bool:
    """True als Porcupine daadwerkelijk gebruikt kan worden."""
    return (
        pvporcupine is not None
        and bool(config.secret("PICOVOICE_ACCESS_KEY"))
        and os.path.exists(_keyword_path())
    )


def detect_wakeword_porcupine(
    keyword_paths: list[str] | None = None,
    sensitivity: float | None = None,
    timeout: float = 30,
) -> bool:
    """Return True zodra het keyword gehoord wordt (of False bij timeout)."""
    if pvporcupine is None:
        raise ImportError("pvporcupine is niet geinstalleerd")

    access_key = config.secret("PICOVOICE_ACCESS_KEY")
    if not access_key:
        raise ValueError("PICOVOICE_ACCESS_KEY ontbreekt (.env / Settings)")

    if keyword_paths is None:
        kw = _keyword_path()
        if not os.path.exists(kw):
            raise ValueError(f"keyword-bestand niet gevonden: {kw}")
        keyword_paths = [kw]

    if sensitivity is None:
        sensitivity = float(config.get("assistant.porcupine_sensitivity", 0.5))

    porcupine = pvporcupine.create(
        access_key=access_key,
        keyword_paths=keyword_paths,
        sensitivities=[sensitivity] * len(keyword_paths),
    )
    try:
        frame_length = porcupine.frame_length
        sample_rate = porcupine.sample_rate
        with sd.RawInputStream(
            samplerate=sample_rate, blocksize=frame_length, dtype="int16", channels=1
        ) as stream:
            start = time.time()
            while not timeout or (time.time() - start) <= timeout:
                pcm = stream.read(frame_length)[0]
                if not pcm:
                    continue
                if porcupine.process(np.frombuffer(pcm, dtype=np.int16)) >= 0:
                    return True
            return False
    finally:
        try:
            porcupine.delete()
        except Exception:  # noqa: BLE001
            pass
