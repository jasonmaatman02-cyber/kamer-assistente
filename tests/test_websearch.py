"""Internet zoeken: een ontbrekend pakket / geen internet is geen 'niks gevonden'; de dashboard-requirements
bevatten het zoekpakket (op de Pi ontbrak ddgs waardoor de AI-tool altijd faalde)."""
from pathlib import Path

import pytest

import config
import logic.websearch as ws
from ai import llm


def test_a_missing_search_package_is_reported_as_such(monkeypatch):
    def boom(query, limit):
        raise ImportError("No module named 'ddgs'")

    monkeypatch.setattr(ws, "_search_duckduckgo", boom)
    out = ws.search_and_summarise("weer morgen")
    assert "niet geinstalleerd" in out and "niks vinden" not in out
    assert ws.last_error


def test_network_failure_is_reported_as_unreachable_not_as_no_results(monkeypatch):
    def boom(query, limit):
        raise ConnectionError("Name or service not known")

    monkeypatch.setattr(ws, "_search_duckduckgo", boom)
    out = ws.search_and_summarise("weer morgen")
    assert "niet bereikbaar" in out and "niks vinden" not in out


def test_no_results_is_still_no_results(monkeypatch):
    monkeypatch.setattr(ws, "_search_duckduckgo", lambda q, n: [])
    assert ws.search_and_summarise("xyzzy") == "Ik kon niks vinden op het internet."
    assert ws.last_error is None


def test_error_state_is_reset_by_a_successful_search(monkeypatch):
    monkeypatch.setattr(ws, "_search_duckduckgo", lambda q, n: (_ for _ in ()).throw(ConnectionError("x")))
    ws.search("a")
    assert ws.last_error
    monkeypatch.setattr(ws, "_search_duckduckgo", lambda q, n: ["snippet"])
    assert ws.search("b") == ["snippet"] and ws.last_error is None


def test_a_summary_uses_the_snippets(monkeypatch):
    seen = {}
    monkeypatch.setattr(ws, "_search_duckduckgo", lambda q, n: ["Het wordt zonnig."])
    monkeypatch.setattr(llm, "complete", lambda prompt, system=None: seen.setdefault("p", prompt) and "Zonnig!")
    assert ws.search_and_summarise("weer") == "Zonnig!"
    assert "Het wordt zonnig." in seen["p"]


@pytest.mark.parametrize("raw", ["abc", None, "", 0, -3, 10 ** 9])
def test_a_garbage_max_results_setting_cannot_break_search(monkeypatch, raw):
    got = {}
    monkeypatch.setattr(ws, "_search_duckduckgo", lambda q, n: got.setdefault("n", n) and ["x"])
    config.set("search.max_results", raw)
    assert ws.search("q") == ["x"] and 1 <= got["n"] <= 20


def test_dashboard_requirements_contain_the_search_package():
    text = (Path(__file__).resolve().parents[1] / "requirements-dashboard.txt").read_text(encoding="utf-8")
    assert any(line.strip().lower().startswith("ddgs") for line in text.splitlines())
