"""Web search + summarise.

Providers (``config: search.provider``):

* ``duckduckgo`` – free, no API key. Default.
* ``serper``     – needs ``SERPER_API_KEY``.

``search(query)`` returns a list of snippet strings.
``search_and_summarise(query)`` runs the snippets through the LLM.
"""
from __future__ import annotations

import requests

import config
from ai import llm


def _search_duckduckgo(query: str, limit: int) -> list[str]:
    from ddgs import DDGS

    out: list[str] = []
    with DDGS() as ddgs:
        for hit in ddgs.text(query, region="nl-nl", max_results=limit):
            body = (hit.get("body") or "").strip()
            if body:
                out.append(body)
    return out


def _search_serper(query: str, limit: int) -> list[str]:
    r = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": config.secret("SERPER_API_KEY"), "Content-Type": "application/json"},
        json={"q": query},
        timeout=10,
    )
    r.raise_for_status()
    return [
        item["snippet"]
        for item in r.json().get("organic", [])[:limit]
        if item.get("snippet")
    ]


def search(query: str) -> list[str]:
    limit = int(config.get("search.max_results", 5))
    provider = (config.get("search.provider") or "duckduckgo").lower()
    try:
        if provider == "serper" and config.secret("SERPER_API_KEY"):
            return _search_serper(query, limit)
        return _search_duckduckgo(query, limit)
    except Exception as exc:  # noqa: BLE001
        print(f"[websearch] '{provider}' faalde: {exc}")
        return []


def search_and_summarise(query: str) -> str:
    print(f"[websearch] zoeken: {query}")
    snippets = search(query)
    if not snippets:
        return "Ik kon niks vinden op het internet."
    prompt = (
        "Gebruik onderstaande info om kort en duidelijk antwoord te geven op de vraag.\n\n"
        f"Vraag: {query}\n\nInfo uit het web:\n" + "\n".join(snippets) +
        "\n\nAntwoord in duidelijke zinnen, zonder opsommingstekens en zonder links:"
    )
    return llm.complete(prompt, system="Je bent een AI-assistent die netjes en beknopt samenvat.")
