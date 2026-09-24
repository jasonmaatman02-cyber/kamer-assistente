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

    monkeypatch.setattr(ollama, "Client", lambda host=None, **kw: _FakeOllamaClient(["Hal", "lo daar"]))

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

    monkeypatch.setattr(ollama, "Client", lambda host=None, **kw: _FakeOllamaClient([]))
    monkeypatch.setattr(llm, "chat", lambda messages, tools=None: {"content": "toch een antwoord", "tool_calls": []})

    events = list(llm.chat_stream([{"role": "user", "content": "hoi"}]))
    assert any(e["type"] == "chunk" and e["text"] == "toch een antwoord" for e in events)
    assert any(e["type"] == "done" and e["content"] == "toch een antwoord" for e in events)


# --------------------------------------------------------------------------- #
# Sessie 2: begrensde concurrency, eindige timeouts, key-wissel
# --------------------------------------------------------------------------- #
import threading
import time


def _slow_llm(monkeypatch, seconds, started=None, fail=False):
    def chat(messages, tools=None):
        if started is not None:
            started.append(1)
        time.sleep(seconds)
        if fail:
            raise RuntimeError("ollama stuk")
        return {"content": "antwoord", "tool_calls": []}

    monkeypatch.setattr(llm, "chat", chat)


def test_llm_turns_are_capped_and_the_rest_gets_a_fast_busy_message(monkeypatch):
    """Elke AI-beurt houdt een waitress-worker vast voor de hele LLM-aanroep;
    zonder limiet zette een reeks chats alle 16 workers vast (dashboard
    bevroor, lokaal gereproduceerd met tools/stress_dashboard.py ollama)."""
    from logic import gpt_handler as g

    monkeypatch.setattr(g, "_SLOT_WAIT_S", 0.3)
    started = []
    _slow_llm(monkeypatch, 1.0, started)

    results = [None] * 8
    times = [0.0] * 8

    def ask(i):
        t0 = time.monotonic()
        results[i] = g.verwerk_input("hoi", session=f"tab{i}")
        times[i] = time.monotonic() - t0

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    busy = [r for r in results if r == g._BUSY_GLOBAL]
    ok = [r for r in results if r == "antwoord"]
    assert len(ok) == 2 and len(busy) == 6, results     # cap = 2 gelijktijdig
    assert len(started) == 2
    assert sorted(times)[3] < 0.9, "geweigerde beurten wachtten te lang"


def test_second_request_in_the_same_session_is_told_to_wait_not_blocked(monkeypatch):
    from logic import gpt_handler as g

    monkeypatch.setattr(g, "_SESSION_WAIT_S", 0.2)
    _slow_llm(monkeypatch, 1.0)
    first = threading.Thread(target=lambda: g.verwerk_input("vraag 1", session="s1"))
    first.start()
    time.sleep(0.15)

    t0 = time.monotonic()
    second = g.verwerk_input("vraag 2", session="s1")   # bv. opnieuw versturen na een browser-timeout
    assert second == g._BUSY_SESSION
    assert time.monotonic() - t0 < 0.8
    first.join(5)
    assert all(m["content"] != "vraag 2" for m in g.history("s1")), "geweigerde vraag mag niet in de historie"


def test_slot_and_session_lock_are_released_after_an_llm_error(monkeypatch):
    from logic import gpt_handler as g

    _slow_llm(monkeypatch, 0.0, fail=True)
    for _ in range(6):                                    # meer dan de cap: een lek zou het opraken
        assert "Fout bij verwerken input" in g.verwerk_input("hoi", session="s2")
    _slow_llm(monkeypatch, 0.0)
    assert g.verwerk_input("hoi", session="s2") == "antwoord"


def test_stream_releases_its_slot_when_the_client_disconnects(monkeypatch):
    from logic import gpt_handler as g

    def endless_stream(messages, tools=None):
        for i in range(1000):
            yield {"type": "chunk", "text": f"{i} "}
            time.sleep(0.01)

    monkeypatch.setattr(llm, "chat_stream", endless_stream)
    for i in range(6):                                    # 6 x afgebroken stream, cap is 2
        gen = g.verwerk_input_stream("hoi", session=f"s{i}")
        next(gen)                                         # eerste stukje ontvangen
        gen.close()                                       # client verbreekt de verbinding
    _slow_llm(monkeypatch, 0.0)
    assert g.verwerk_input("hoi", session="daarna") == "antwoord"


def test_stream_busy_message_is_yielded_not_raised(monkeypatch):
    from logic import gpt_handler as g

    monkeypatch.setattr(g, "_SESSION_WAIT_S", 0.1)
    gate = threading.Event()

    def blocking_stream(messages, tools=None):
        gate.wait(3)
        yield {"type": "done", "content": "klaar"}

    monkeypatch.setattr(llm, "chat_stream", blocking_stream)
    t = threading.Thread(target=lambda: list(g.verwerk_input_stream("a", session="zelfde")))
    t.start()
    time.sleep(0.15)
    assert list(g.verwerk_input_stream("b", session="zelfde")) == [g._BUSY_SESSION]
    gate.set()
    t.join(3)


def test_ollama_client_gets_a_finite_timeout(monkeypatch):
    import config
    import ollama

    seen = {}

    class FakeClient:
        def __init__(self, host=None, **kw):
            seen.update(kw)

        def chat(self, **kw):
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(ollama, "Client", FakeClient)
    llm._chat_ollama([{"role": "user", "content": "hoi"}], None)
    t = seen["timeout"]
    assert t is not None
    read = getattr(t, "read", t)
    assert 300 <= read <= 600, "dekt de gemeten koude start (~225s) met marge"

    config.set("ai.ollama_timeout_s", 90)
    llm._chat_ollama([{"role": "user", "content": "hoi"}], None)
    assert getattr(seen["timeout"], "read", seen["timeout"]) == 90


def test_openai_client_is_rebuilt_when_the_key_changes_and_has_a_timeout(monkeypatch):
    import sys
    import types

    built = []

    class FakeOpenAI:
        def __init__(self, api_key=None, **kw):
            built.append((api_key, kw))

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(llm, "_openai_client", None)
    monkeypatch.setattr(llm, "_openai_key", None)

    monkeypatch.setenv("OPENAI_API_KEY", "sleutel-1")
    c1 = llm._get_openai()
    assert llm._get_openai() is c1                        # zelfde key -> hergebruik
    monkeypatch.setenv("OPENAI_API_KEY", "sleutel-2")     # gewijzigd via Settings
    c2 = llm._get_openai()
    assert c2 is not c1 and built[-1][0] == "sleutel-2"
    assert built[-1][1]["timeout"] <= 120 and built[-1][1]["max_retries"] <= 2
