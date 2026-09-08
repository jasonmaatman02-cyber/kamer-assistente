"""Backwards-compatible shim -> :mod:`ai.tts`.

The real implementation now lives in ``ai/tts.py`` and is backend-switchable
(piper / espeak / openai) via ``settings.json``.
"""
from ai.tts import TextToSpeech, speak, synthesize, volume_tts

__all__ = ["TextToSpeech", "speak", "synthesize", "volume_tts"]
