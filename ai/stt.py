"""Unified speech-to-text.

Backends (``config: stt.backend``):

* ``faster_whisper`` – local, free, default. CTranslate2, light enough for a Pi 4B.
* ``whisper``        – local openai-whisper (heavier, needs torch).
* ``openai``         – Whisper API, needs ``OPENAI_API_KEY``.

Public API::

    transcribe(wav_path, language=None)      -> str
    transcribe_array(samples, samplerate)    -> str
"""
from __future__ import annotations

import os
import tempfile

import config

_fw_model = None
_fw_key = None
_whisper_model = None
_whisper_size = None


# --------------------------------------------------------------------------- #
# faster-whisper
# --------------------------------------------------------------------------- #
def _get_fw():
    global _fw_model, _fw_key
    size = config.get("stt.model", "tiny")
    device = config.get("stt.device", "cpu")
    compute = config.get("stt.compute_type", "int8")
    key = (size, device, compute)
    if _fw_model is not None and _fw_key == key:
        return _fw_model
    from faster_whisper import WhisperModel

    _fw_model = WhisperModel(size, device=device, compute_type=compute)
    _fw_key = key
    return _fw_model


def _tr_faster_whisper(path: str, language: str) -> str:
    model = _get_fw()
    segments, _info = model.transcribe(path, language=language, vad_filter=True)
    return " ".join(seg.text for seg in segments).strip()


# --------------------------------------------------------------------------- #
# openai-whisper (local)
# --------------------------------------------------------------------------- #
def _tr_whisper(path: str, language: str) -> str:
    global _whisper_model, _whisper_size
    size = config.get("stt.model", "tiny")
    if _whisper_model is None or _whisper_size != size:
        import whisper

        _whisper_model = whisper.load_model(size)
        _whisper_size = size
    result = _whisper_model.transcribe(path, language=language, fp16=False)
    return (result.get("text") or "").strip()


# --------------------------------------------------------------------------- #
# OpenAI API
# --------------------------------------------------------------------------- #
def _tr_openai(path: str, language: str) -> str:
    import requests

    key = config.secret("OPENAI_API_KEY")
    with open(path, "rb") as f:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={
                "file": (os.path.basename(path), f, "audio/wav"),
                "model": (None, "whisper-1"),
                "language": (None, language),
            },
            timeout=30,
        )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
def transcribe(wav_path: str, language: str | None = None) -> str:
    language = language or config.get("stt.language", "nl")
    backend = (config.get("stt.backend") or "faster_whisper").lower()
    order = {
        "faster_whisper": [_tr_faster_whisper, _tr_whisper, _tr_openai],
        "whisper": [_tr_whisper, _tr_faster_whisper, _tr_openai],
        "openai": [_tr_openai, _tr_faster_whisper, _tr_whisper],
    }.get(backend, [_tr_faster_whisper, _tr_whisper, _tr_openai])

    last_err = None
    for fn in order:
        try:
            return fn(wav_path, language)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"[stt] {fn.__name__} faalde: {exc}")
    print(f"[stt] alle backends faalden: {last_err}")
    return ""


def transcribe_array(samples, samplerate: int, language: str | None = None) -> str:
    """``samples`` is a float32 numpy array in [-1, 1]."""
    import numpy as np
    import scipy.io.wavfile as wav

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        wav.write(path, samplerate, np.int16(np.clip(samples, -1, 1) * 32767))
        return transcribe(path, language)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
