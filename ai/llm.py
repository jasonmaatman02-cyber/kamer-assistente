"""Unified chat LLM interface.

Backends (``config: ai.backend``):

* ``ollama``  – local, free, default. Needs the Ollama daemon running.
* ``openai``  – fallback, needs ``OPENAI_API_KEY``.

Public API::

    chat(messages, tools=None)  -> {"content": str|None, "tool_calls": [ {name, arguments} ]}
    complete(prompt, system=None) -> str
"""
from __future__ import annotations

import json
import re

import config

_openai_client = None


def _salvage_tool_calls(content: str):
    """Small local models sometimes print a tool call as JSON text instead of
    using the tool protocol. Try to recover ``{name, arguments|parameters}``."""
    if not content or "{" not in content:
        return []
    for match in re.finditer(r"\{(?:[^{}]|\{[^{}]*\})*\}", content):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("name"), str):
            args = obj.get("arguments")
            if args is None:
                args = obj.get("parameters")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if isinstance(args, dict):
                return [{"name": obj["name"], "arguments": args}]
    return []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_openai_tools(tools):
    """Accept the project's flat ``[{name, description, parameters}]`` list and
    return OpenAI/Ollama tool schema. Passes through already-wrapped tools."""
    if not tools:
        return None
    wrapped = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            wrapped.append(t)
        else:
            wrapped.append({"type": "function", "function": t})
    return wrapped


def _empty():
    return {"content": None, "tool_calls": [], "raw": None}


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #
def _chat_ollama(messages, tools):
    import ollama

    client = ollama.Client(host=config.get("ai.ollama_url"))
    resp = client.chat(
        model=config.get("ai.ollama_model"),
        messages=messages,
        tools=_as_openai_tools(tools),
        options={"temperature": config.get("ai.temperature", 0.6)},
    )
    msg = resp.get("message", {}) if isinstance(resp, dict) else getattr(resp, "message", {})
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    raw_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)

    calls = []
    for c in raw_calls or []:
        fn = c.get("function", {}) if isinstance(c, dict) else getattr(c, "function", {})
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append({"name": name, "arguments": args or {}})

    if not calls:
        calls = _salvage_tool_calls(content or "")
        if calls:
            content = None

    return {"content": content, "tool_calls": calls, "raw": resp}


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #
def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(api_key=config.secret("OPENAI_API_KEY"))
    return _openai_client


def _chat_openai(messages, tools):
    client = _get_openai()
    kwargs = dict(
        model=config.get("ai.openai_model", "gpt-4o-mini"),
        messages=messages,
        temperature=config.get("ai.temperature", 0.6),
    )
    wrapped = _as_openai_tools(tools)
    if wrapped:
        kwargs["tools"] = wrapped
        kwargs["tool_choice"] = "auto"

    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    calls = []
    for c in msg.tool_calls or []:
        args = c.function.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append({"name": c.function.name, "arguments": args or {}})
    return {"content": msg.content, "tool_calls": calls, "raw": resp}


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
def chat(messages, tools=None) -> dict:
    backend = (config.get("ai.backend") or "ollama").lower()
    try:
        if backend == "openai":
            return _chat_openai(messages, tools)
        return _chat_ollama(messages, tools)
    except Exception as exc:  # noqa: BLE001 - surface a usable message to the UI
        print(f"[llm] backend '{backend}' faalde: {exc}")
        hint = ""
        if backend == "ollama" and "not found" in str(exc).lower():
            hint = f" — draai eerst 'ollama pull {config.get('ai.ollama_model')}'"
        # Try the other backend once before giving up.
        try:
            if backend != "openai" and config.secret("OPENAI_API_KEY"):
                print("[llm] val terug op OpenAI")
                return _chat_openai(messages, tools)
        except Exception as exc2:  # noqa: BLE001
            print(f"[llm] fallback faalde ook: {exc2}")
        out = _empty()
        out["content"] = f"AI niet bereikbaar ({exc}){hint}"
        return out


def complete(prompt: str, system: str | None = None) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages).get("content") or ""


def chat_stream(messages, tools=None):
    """Generator. Yields ``{"type": "chunk", "text": str}`` while the model
    writes, then finally either ``{"type": "tool_calls", "calls": [...]}`` (no
    text was meant for the user) or ``{"type": "done", "content": str}``.

    Only Ollama streams token-by-token; OpenAI / errors fall back to one chunk.
    """
    backend = (config.get("ai.backend") or "ollama").lower()
    if backend != "ollama":
        res = chat(messages, tools)
        if res.get("tool_calls"):
            yield {"type": "tool_calls", "calls": res["tool_calls"]}
        else:
            text = res.get("content") or ""
            if text:
                yield {"type": "chunk", "text": text}
            yield {"type": "done", "content": text}
        return

    try:
        import ollama

        client = ollama.Client(host=config.get("ai.ollama_url"))
        stream = client.chat(
            model=config.get("ai.ollama_model"),
            messages=messages,
            tools=_as_openai_tools(tools),
            options={"temperature": config.get("ai.temperature", 0.6)},
            stream=True,
        )
        content = ""
        raw_calls = []
        for part in stream:
            msg = part.get("message", {}) if isinstance(part, dict) else getattr(part, "message", {})
            delta = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            tcs = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
            if tcs:
                raw_calls.extend(tcs)
            if delta:
                content += delta
                yield {"type": "chunk", "text": delta}

        calls = []
        for c in raw_calls:
            fn = c.get("function", {}) if isinstance(c, dict) else getattr(c, "function", {})
            name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
            args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"name": name, "arguments": args or {}})
        if not calls:
            calls = _salvage_tool_calls(content)

        if calls:
            yield {"type": "tool_calls", "calls": calls}
        else:
            yield {"type": "done", "content": content}
    except Exception as exc:  # noqa: BLE001
        res = chat(messages, tools)  # niet-streamende fallback (met OpenAI-fallback erin)
        if res.get("tool_calls"):
            yield {"type": "tool_calls", "calls": res["tool_calls"]}
        else:
            text = res.get("content") or f"AI-fout: {exc}"
            yield {"type": "chunk", "text": text}
            yield {"type": "done", "content": text}
