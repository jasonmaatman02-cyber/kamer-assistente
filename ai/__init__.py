"""Provider-agnostic AI helpers (LLM chat, text-to-speech, speech-to-text).

Every backend is chosen at call time from :mod:`config`, so switching between
the free/offline stack (Ollama, Piper, faster-whisper) and the paid OpenAI
stack is a settings change, not a code change.
"""
