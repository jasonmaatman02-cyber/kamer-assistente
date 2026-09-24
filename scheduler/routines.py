import asyncio

import config
from scheduler.alarm_manager import set_alarm
from logic.logger import log
from logic.notes import get_notes
from voice.tts_output import speak
from voice.Whisper_short import shortwhisper
from sound_system.radio import RadioPlayer
from devices.Lights import SlimmeLamp, describe_lamp_error


def _say_generated(prompt: str, fallback: str) -> None:
    """Laat het model een groet bedenken en spreek die uit. Faalt het model (Ollama uit, geen
    internet), dan spreekt de routine een vaste groet uit i.p.v. de foutmelding
    (``vraag_aan_gpt`` geeft "Sorry, ik kan nu geen antwoord geven: HTTPConnectionPool(...)"
    terug, en dat werd om 07:00 als wekker hardop voorgelezen) en meldt de stap alsnog als
    mislukt (de exceptie gaat door naar ``_step``)."""
    from ai import llm

    try:
        text = (llm.complete(prompt) or "").strip()
    except Exception:
        speak(fallback)
        raise
    speak(text or fallback)


def _all_lamps():
    creds = (config.secret("TAPO_USER"), config.secret("TAPO_PASSWORD"))
    for entry in config.get("devices.lamps", []) or []:
        ip = entry.get("ip") if isinstance(entry, dict) else None
        if ip:      # een lamp zonder ip (of een kapotte entry) mag de hele routine niet laten crashen
            yield SlimmeLamp(*creds, ip)


def _for_each_lamp(coro_name: str, failures: list | None = None):
    for lamp in _all_lamps():
        try:
            asyncio.run(lamp.connect())
            asyncio.run(getattr(lamp, coro_name)())
        except Exception as exc:  # noqa: BLE001
            log("ROUTINE", f"Light action failed: {exc}")
            log("ROUTINE", "Continuing with next action")
            if failures is not None:
                failures.append(describe_lamp_error(exc, getattr(lamp, 'ip', None)))


def _step(what: str, fn, failures: list | None = None) -> None:
    """Eén routine-stap draaien; een fout mag de rest van de routine niet
    stoppen (spec: 'Tapo werkt niet' mag niet 'hele routine stopt' betekenen).
    Een mislukte stap wordt wel in ``failures`` gemeld, zodat de aanroeper
    (dashboard-knop, AI-tool) niet 'gelukt' meldt terwijl er iets niet gebeurde."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        log("ROUTINE", f"{what} failed: {exc}")
        log("ROUTINE", "Continuing with next action")
        if failures is not None:
            failures.append(f"{what}: {exc}")


def morning_routine() -> list[str]:
    """Voert de ochtend-routine uit; geeft de lijst mislukte stappen terug (leeg = alles gelukt)."""
    log("ROUTINE", "Starting morning routine")
    failures: list[str] = []

    _step("Greeting", lambda: _say_generated(
        "Bedenk een kort, grappig zinnetje om te zeggen bij het wakker worden. Max 3 zinnen.",
        "Goedemorgen!",
    ), failures)

    if config.get("features.radio", True):
        _step("Radio", lambda: RadioPlayer().play("radio538"), failures)

    def _notes():
        notes = get_notes("default")
        if notes:
            speak("Hier zijn je notities voor vandaag.")
            for note in notes:
                speak(f"{note.get('timestamp', '')}: {note.get('note', '')}".strip(": "))
        else:
            speak("Je hebt geen notities voor vandaag.")

    _step("Notes", _notes, failures)
    log("ROUTINE", "Finished morning routine")
    return failures


def bedtime_routine() -> list[str]:
    """Voert de bedtijd-routine uit; geeft de lijst mislukte stappen terug (leeg = alles gelukt)."""
    from logic.ask_gpt import vraag_aan_gpt

    log("ROUTINE", "Starting bedtime routine")
    failures: list[str] = []

    _step("Greeting", lambda: _say_generated(
        "Bedenk een kort, grappig zinnetje om welterusten te wensen bij het slapengaan.",
        "Welterusten!",
    ), failures)

    _for_each_lamp("uit", failures)

    # Vanaf het dashboard (geen microfoon) heeft de vraag-en-antwoord geen zin.
    from voice.Whisper import mic_available

    if not mic_available():
        speak("Slaap lekker!")
        log("ROUTINE", "Finished bedtime routine")
        return failures

    try:
        speak("Zal ik ook een wekker voor je instellen?")
        response = shortwhisper() or ""
        if not any(w in response.lower() for w in ("ja", "graag", "zeker")):
            speak("Oké, slaap lekker!")
            log("ROUTINE", "Finished bedtime routine")
            return failures

        speak("Hoe laat wil je wakker worden?")
        time_response = (shortwhisper() or "").replace("om", "").strip()
        if not time_response:
            speak("Ik heb geen tijd gehoord. Geen wekker gezet. Slaap lekker!")
            log("ROUTINE", "Finished bedtime routine")
            return failures

        wake_time = vraag_aan_gpt(
            f"Zet dit om naar uur:minuut en geef ALLEEN uur:minuut terug, niks anders: {time_response}"
        )
        scheduled = set_alarm(wake_time, morning_routine)
        if scheduled is None:
            speak(f"Ik kon '{time_response}' niet omzetten naar een tijd. Geen wekker gezet.")
            log("ROUTINE", f"Alarm parsing failed for: {time_response!r} -> {wake_time!r}")
        else:
            hhmm = scheduled.strftime("%H:%M")
            speak(f"Wekker gezet voor {hhmm}. Slaap lekker!")
            log("ROUTINE", f"Alarm set for {hhmm}")
    except Exception as exc:  # noqa: BLE001 - de mic-Q&A mag de rest niet meeslepen
        log("ROUTINE", f"Alarm Q&A failed: {exc}")
        failures.append(f"Wekker instellen: {exc}")
    log("ROUTINE", "Finished bedtime routine")
    return failures
