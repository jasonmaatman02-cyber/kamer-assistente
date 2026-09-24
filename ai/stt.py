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
import threading
import time

import config

_fw_model = None
_fw_key = None
_whisper_model = None
_whisper_size = None

# De wake-loop, de bedtijd-routine (dashboard-thread) en een handmatige opdracht
# kunnen tegelijk transcriberen; zonder lock laadden twee threads tegelijk elk hun
# eigen model (dubbel RAM op een Pi) en herstartte een niet-ladend model (geen
# internet + model niet gecached, OOM) elke ~2s een nieuwe laadpoging.
_load_lock = threading.Lock()
_LOAD_RETRY_S = 60.0
_load_failed: dict[str, tuple[float, str]] = {}


def _check_load_backoff(name: str) -> None:
    failed = _load_failed.get(name)
    if failed and time.monotonic() < failed[0]:
        raise RuntimeError(f"{name} kon niet laden ({failed[1]}); volgende poging over "
                           f"{int(failed[0] - time.monotonic())}s")


# --------------------------------------------------------------------------- #
# faster-whisper
# --------------------------------------------------------------------------- #
def _get_fw():
    global _fw_model, _fw_key
    size = config.get("stt.model", "tiny")
    device = config.get("stt.device", "cpu")
    compute = config.get("stt.compute_type", "int8")
    key = (size, device, compute)
    with _load_lock:
        if _fw_model is not None and _fw_key == key:
            return _fw_model
        _check_load_backoff("faster_whisper")
        try:
            from faster_whisper import WhisperModel

            _fw_model = WhisperModel(size, device=device, compute_type=compute)
        except Exception as exc:  # noqa: BLE001
            _load_failed["faster_whisper"] = (time.monotonic() + _LOAD_RETRY_S, str(exc)[:200])
            raise
        _load_failed.pop("faster_whisper", None)
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
    with _load_lock:
        if _whisper_model is None or _whisper_size != size:
            _check_load_backoff("whisper")
            try:
                import whisper

                _whisper_model = whisper.load_model(size)
            except Exception as exc:  # noqa: BLE001
                _load_failed["whisper"] = (time.monotonic() + _LOAD_RETRY_S, str(exc)[:200])
                raise
            _load_failed.pop("whisper", None)
            _whisper_size = size
        model = _whisper_model
    result = model.transcribe(path, language=language, fp16=False)
    return (result.get("text") or "").strip()


# --------------------------------------------------------------------------- #
# OpenAI API
# --------------------------------------------------------------------------- #
def _tr_openai(path: str, language: str) -> str:
    import requests

    key = config.secret("OPENAI_API_KEY")
    if not key:
        # Zonder deze check ging er elke keer een ongeauthenticeerde request (en de
        # audio) naar OpenAI, alleen om een 401 terug te krijgen.
        raise RuntimeError("OPENAI_API_KEY ontbreekt")
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
def transcribe(wav_path: str, language: str | None = None, cloud_fallback: bool = True) -> str:
    """Transcribeer een wav. ``cloud_fallback=False`` sluit de OpenAI-API uit als
    *terugval* (alleen ``stt.backend: openai`` gebruikt 'm dan nog): nodig voor het
    continu meeluisteren naar het wake-word -- als het lokale model faalt zou dat
    anders 24/7 kamergeluid naar een betaalde cloud-API sturen."""
    language = language or config.get("stt.language", "nl")
    backend = (config.get("stt.backend") or "faster_whisper").lower()
    order = {
        "faster_whisper": [_tr_faster_whisper, _tr_whisper, _tr_openai],
        "whisper": [_tr_whisper, _tr_faster_whisper, _tr_openai],
        "openai": [_tr_openai, _tr_faster_whisper, _tr_whisper],
    }.get(backend, [_tr_faster_whisper, _tr_whisper, _tr_openai])
    if not cloud_fallback and backend != "openai":
        order = [fn for fn in order if fn is not _tr_openai]

    last_err = None
    for fn in order:
        try:
            return fn(wav_path, language)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"[stt] {fn.__name__} faalde: {exc}")
    print(f"[stt] alle backends faalden: {last_err}")
    return ""


def transcribe_array(samples, samplerate: int, language: str | None = None,
                     cloud_fallback: bool = True) -> str:
    """``samples`` is a float32 numpy array in [-1, 1]."""
    import numpy as np
    import scipy.io.wavfile as wav

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        wav.write(path, samplerate, np.int16(np.clip(samples, -1, 1) * 32767))
        return transcribe(path, language, cloud_fallback=cloud_fallback)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
