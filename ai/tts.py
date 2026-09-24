"""Unified text-to-speech.

Backends (``config: tts.backend``):

* ``piper``  – local, free, default. Downloads the voice model once to ``models/``.
* ``espeak`` – local, free, robotic. Uses the ``espeak-ng`` binary.
* ``openai`` – needs ``OPENAI_API_KEY``.
* ``none``   – silent (logs only).

Public API::

    speak(text)                 # synthesize + play (blocking)
    synthesize(text, out_path)  # write an audio file, return its path
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import config

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"

_piper_voice = None
_piper_voice_name = None
_mixer_ready = None

# Spraak kan niet zinvol overlappen en pygame.mixer.music is één globaal
# kanaal: zonder lock haalt een wekker die afgaat tijdens een routine (of twee
# gelijktijdige dashboard-requests) elkaars afspelen onderuit. Zelfde lock
# beschermt het (eenmalige, ~60MB) laden van de Piper-stem tegen dubbel laden.
_speak_lock = threading.Lock()
_piper_lock = threading.Lock()
_PLAY_MAX_S = 180   # harde bovengrens voor één afspeelbeurt (zie _play)


# --------------------------------------------------------------------------- #
# Piper
# --------------------------------------------------------------------------- #
def _piper_model_path(name: str) -> Path | None:
    MODELS_DIR.mkdir(exist_ok=True)
    onnx = MODELS_DIR / f"{name}.onnx"
    if onnx.exists():
        return onnx
    try:
        from piper.download_voices import download_voice

        print(f"[tts] piper-stem '{name}' downloaden (eenmalig)...")
        download_voice(name, MODELS_DIR)
        return onnx if onnx.exists() else None
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] kon piper-stem niet downloaden: {exc}")
        return None


def _get_piper_voice():
    global _piper_voice, _piper_voice_name
    name = config.get("tts.piper_model")
    if _piper_voice is not None and _piper_voice_name == name:
        return _piper_voice
    with _piper_lock:
        if _piper_voice is not None and _piper_voice_name == name:
            return _piper_voice
        from piper import PiperVoice

        path = _piper_model_path(name)
        if not path:
            raise RuntimeError(f"piper-stem '{name}' niet beschikbaar")
        _piper_voice = PiperVoice.load(str(path))
        _piper_voice_name = name
        return _piper_voice


def _synth_piper(text: str, out_path: str) -> str:
    voice = _get_piper_voice()
    syn_config = None
    try:
        from piper import SynthesisConfig

        spk = config.get("tts.piper_speaker", 0)
        syn_config = SynthesisConfig(
            speaker_id=spk if spk else None,
            volume=float(config.get("tts.volume", 1.0)),
        )
    except Exception:  # noqa: BLE001
        syn_config = None
    with wave.open(out_path, "wb") as wf:
        if syn_config is not None:
            voice.synthesize_wav(text, wf, syn_config)
        else:
            voice.synthesize_wav(text, wf)
    return out_path


# --------------------------------------------------------------------------- #
# espeak-ng
# --------------------------------------------------------------------------- #
def _synth_espeak(text: str, out_path: str) -> str:
    lang = config.get("tts.language", "nl")
    subprocess.run(
        # '--': tekst (LLM-antwoord, notitie, routine-stap) die met '-' begint
        # mag nooit als espeak-optie geparsed worden (bv. '-w<pad>' overschrijft
        # een bestand).
        ["espeak-ng", "-v", lang, "-w", out_path, "--", text],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out_path


def _speak_espeak(text: str) -> None:
    lang = config.get("tts.language", "nl")
    subprocess.run(["espeak-ng", "-v", lang, "--", text], check=True, capture_output=True, timeout=15)


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #
def _synth_openai(text: str, out_path: str) -> str:
    import requests

    key = config.secret("OPENAI_API_KEY")
    voice = config.get("tts.openai_voice", "onyx")
    resp = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "tts-1", "input": text, "voice": voice, "response_format": "mp3"},
        timeout=30,
    )
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return out_path


# --------------------------------------------------------------------------- #
# Playback
# --------------------------------------------------------------------------- #
def _play(path: str) -> None:
    global _mixer_ready
    try:
        import pygame

        if _mixer_ready is None:
            if os.name == "nt":
                os.environ.setdefault("SDL_AUDIODRIVER", "directsound")
            pygame.mixer.init()
            _mixer_ready = True
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(float(config.get("tts.volume", 1.0)))
        pygame.mixer.music.play()
        # Harde bovengrens: een audio-device dat halverwege verdwijnt kan
        # get_busy() voor altijd True laten -- dan hing deze thread (en, met
        # _speak_lock, alle latere spraak) voor eeuwig.
        deadline = time.monotonic() + _PLAY_MAX_S
        while pygame.mixer.music.get_busy():
            if time.monotonic() > deadline:
                print(f"[tts] afspelen duurde >{_PLAY_MAX_S}s; gestopt")
                pygame.mixer.music.stop()
                break
            time.sleep(0.1)
        pygame.mixer.music.unload()
    except Exception as exc:  # noqa: BLE001
        _mixer_ready = False
        print(f"[tts] afspelen mislukt ({exc}); probeer systeemspeler")
        _play_system(path)


def _play_system(path: str) -> None:
    for player in (["aplay", path], ["ffplay", "-nodisp", "-autoexit", path], ["afplay", path]):
        try:
            # timeout: een audio-device dat bezet is (bv. raspotify dat tegelijk
            # ALSA gebruikt) mag deze speler nooit voorgoed laten hangen -- dan
            # gewoon door naar de volgende speler in de lijst.
            subprocess.run(player, check=True, capture_output=True, timeout=20)
            return
        except Exception:  # noqa: BLE001
            continue


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
def synthesize(text: str, out_path: str | None = None) -> str | None:
    backend = (config.get("tts.backend") or "piper").lower()
    if not text or not text.strip() or backend == "none":
        return None
    own_temp = out_path is None
    if own_temp:
        suffix = ".mp3" if backend == "openai" else ".wav"
        fd, out_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
    try:
        if backend == "openai":
            return _synth_openai(text, out_path)
        if backend == "espeak":
            return _synth_espeak(text, out_path)
        return _synth_piper(text, out_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] backend '{backend}' faalde: {exc}")
        if backend != "espeak":
            try:
                return _synth_espeak(text, out_path)
            except Exception as exc2:  # noqa: BLE001
                print(f"[tts] espeak-fallback faalde: {exc2}")
        if own_temp:  # mkstemp maakte al een (leeg) bestand aan -- niet laten liggen
            try:
                os.remove(out_path)
            except OSError:
                pass
        return None


def speak(text: str) -> None:
    backend = (config.get("tts.backend") or "piper").lower()
    if not text or not text.strip():
        return
    if backend == "none":
        print(f"[tts:none] {text}")
        return
    with _speak_lock:
        if backend == "espeak":
            try:
                _speak_espeak(text)
            except Exception as exc:  # noqa: BLE001
                print(f"[tts] espeak faalde: {exc}")
            return
        path = synthesize(text)
        if path:
            try:
                _play(path)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass


# Backwards-compat: old code did ``from voice.tts_output import TextToSpeech``.
class TextToSpeech:
    def __init__(self, lang: str | None = None):
        if lang:
            config.set("tts.language", lang)
        self.init_failed = False

    def speak(self, text: str):
        speak(text)


volume_tts = config.get("tts.volume", 1.0)
