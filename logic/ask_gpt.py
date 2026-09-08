"""Plain LLM Q&A helper (no tools). Kept for backwards compatibility."""
from ai import llm
from logic.logger import log


def vraag_aan_gpt(prompt: str) -> str:
    try:
        antwoord = llm.complete(prompt)
        return antwoord or "Sorry, ik kan nu geen antwoord geven."
    except Exception as exc:  # noqa: BLE001
        log("GPT_ERROR", f"Fout bij LLM-aanroep: {exc}")
        return f"Sorry, ik kan nu geen antwoord geven: {exc}"
