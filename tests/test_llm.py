"""ai/llm.py::chat_stream -- als de Ollama-stream halverwege breekt (netwerk-
hikje, Ollama herstart tijdens het antwoord), mag dat niet leiden tot een
compleet TWEEDE, losstaand antwoord achter het al getoonde partiële
antwoord (zie /api/chat_stream, dat elk stukje direct naar de browser stuurt)."""
import ai.llm as llm


class _FakeMessage(dict):
    """Nabootsing van het ollama-pakket se message-object (dict-achtig)."""


def _fake_stream_then_raise(chunks):
    def _gen():
        for text in chunks:
            yield {"message": {"content": text, "tool_calls": None}}
        raise ConnectionError("verbinding met Ollama verbroken")
    return _gen()


class _FakeOllamaClient:
    def __init__(self, chunks):
        self._chunks = chunks

    def chat(self, **kwargs):
        assert kwargs.get("stream") is True
        return _fake_stream_then_raise(self._chunks)


def test_stream_failure_after_partial_content_does_not_duplicate_answer(monkeypatch):
    import ollama

    monkeypatch.setattr(ollama, "Client", lambda host=None: _FakeOllamaClient(["Hal", "lo daar"]))

    calls = {"n": 0}

    def fallback_should_not_run(messages, tools=None):
        calls["n"] += 1
        return {"content": "EEN HEEL ANDER TWEEDE ANTWOORD", "tool_calls": []}

    monkeypatch.setattr(llm, "chat", fallback_should_not_run)

    events = list(llm.chat_stream([{"role": "user", "content": "hoi"}]))

    chunks = [e["text"] for e in events if e["type"] == "chunk"]
    assert "".join(chunks) == "Hallo daar"
    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["content"] == "Hallo daar"
    assert calls["n"] == 0, "de niet-streamende fallback had NIET nog eens aangeroepen mogen worden"


def test_stream_failure_before_any_content_still_falls_back(monkeypatch):
    """Als er nog NIKS getoond is aan de gebruiker, mag de bestaande fallback
    (een niet-streamende poging, zodat er toch een antwoord komt) wel."""
    import ollama

    monkeypatch.setattr(ollama, "Client", lambda host=None: _FakeOllamaClient([]))
    monkeypatch.setattr(llm, "chat", lambda messages, tools=None: {"content": "toch een antwoord", "tool_calls": []})

    events = list(llm.chat_stream([{"role": "user", "content": "hoi"}]))
    assert any(e["type"] == "chunk" and e["text"] == "toch een antwoord" for e in events)
    assert any(e["type"] == "done" and e["content"] == "toch een antwoord" for e in events)
