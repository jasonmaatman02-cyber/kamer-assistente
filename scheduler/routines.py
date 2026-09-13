import asyncio

import config
from scheduler.alarm_manager import set_alarm
from logic.logger import log
from logic.notes import get_notes
from voice.tts_output import speak
from voice.Whisper_short import shortwhisper
from sound_system.radio import RadioPlayer
from devices.Lights import SlimmeLamp


def _all_lamps():
    creds = (config.secret("TAPO_USER"), config.secret("TAPO_PASSWORD"))
    for entry in config.get("devices.lamps", []):
        yield SlimmeLamp(*creds, entry["ip"])


def _for_each_lamp(coro_name: str):
    for lamp in _all_lamps():
        try:
            asyncio.run(lamp.connect())
            asyncio.run(getattr(lamp, coro_name)())
        except Exception as exc:  # noqa: BLE001
            log("ROUTINE", f"Light action failed: {exc}")
            log("ROUTINE", "Continuing with next action")


def _step(what: str, fn) -> None:
    """Eén routine-stap draaien; een fout mag de rest van de routine niet
    stoppen (spec: 'Tapo werkt niet' mag niet 'hele routine stopt' betekenen)."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        log("ROUTINE", f"{what} failed: {exc}")
        log("ROUTINE", "Continuing with next action")


def morning_routine():
    from logic.ask_gpt import vraag_aan_gpt

    log("ROUTINE", "Starting morning routine")

    _step("Greeting", lambda: speak(vraag_aan_gpt(
        "Bedenk een kort, grappig zinnetje om te zeggen bij het wakker worden. Max 3 zinnen."
    )))

    if config.get("features.radio", True):
        _step("Radio", lambda: RadioPlayer().play("radio538"))

    def _notes():
        notes = get_notes("default")
        if notes:
            speak("Hier zijn je notities voor vandaag.")
            for note in notes:
                speak(f"{note['timestamp']}: {note['note']}")
        else:
            speak("Je hebt geen notities voor vandaag.")

    _step("Notes", _notes)
    log("ROUTINE", "Finished morning routine")


def bedtime_routine():
    from logic.ask_gpt import vraag_aan_gpt

    log("ROUTINE", "Starting bedtime routine")

    _step("Greeting", lambda: speak(vraag_aan_gpt(
        "Bedenk een kort, grappig zinnetje om welterusten te wensen bij het slapengaan."
    )))

    _for_each_lamp("uit")

    # Vanaf het dashboard (geen microfoon) heeft de vraag-en-antwoord geen zin.
    from voice.Whisper import mic_available

    if not mic_available():
        speak("Slaap lekker!")
        log("ROUTINE", "Finished bedtime routine")
        return

    try:
        speak("Zal ik ook een wekker voor je instellen?")
        response = shortwhisper() or ""
        if not any(w in response.lower() for w in ("ja", "graag", "zeker")):
            speak("Oké, slaap lekker!")
            log("ROUTINE", "Finished bedtime routine")
            return

        speak("Hoe laat wil je wakker worden?")
        time_response = (shortwhisper() or "").replace("om", "").strip()
        if not time_response:
            speak("Ik heb geen tijd gehoord. Geen wekker gezet. Slaap lekker!")
            log("ROUTINE", "Finished bedtime routine")
            return

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
    log("ROUTINE", "Finished bedtime routine")
