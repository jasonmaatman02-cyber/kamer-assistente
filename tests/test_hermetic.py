"""De testsuite is hermetisch: geen echt netwerk, geen echte Ollama (de Pi draait die wel)."""
import socket

import pytest

import config


def test_external_hosts_are_unreachable_from_tests():
    with pytest.raises(OSError, match="geblokkeerd"):
        socket.create_connection(("192.0.2.1", 80), timeout=1)
    with pytest.raises(socket.gaierror, match="geblokkeerd"):
        socket.getaddrinfo("api.spotify.com", 443)


def test_loopback_still_works():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.close()
    finally:
        server.close()


def test_ollama_is_closed_in_tests_so_an_unmocked_call_fails_fast_instead_of_asking_a_real_model():
    from ai import llm

    assert config.get("ai.ollama_url") == "http://127.0.0.1:9"
    res = llm.chat([{"role": "user", "content": "hoi"}])
    assert res["error"] and "niet bereikbaar" in res["error"]
