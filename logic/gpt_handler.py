"""Natural-language command handler.

The user's text goes to the LLM (:mod:`ai.llm`, Ollama by default) with a set of
tool definitions. If the model picks a tool we run it and return its result;
otherwise we return the model's plain reply.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import config
from ai import llm
from logic.logger import log
from logic.notes import add_note, list_all_notes
from logic import websearch

# --------------------------------------------------------------------------- #
# Lazy singletons — importing this module must stay cheap and offline-safe.
# --------------------------------------------------------------------------- #
_cache: dict = {}


def _get(name: str):
    if name in _cache:
        return _cache[name]
    if name == "radio":
        from sound_system.radio import RadioPlayer

        _cache[name] = RadioPlayer()
    elif name == "spotify":
        from sound_system.muziek import SpotifyDJ

        _cache[name] = SpotifyDJ()
    elif name == "weer":
        from weer.weer import WeerAPI

        _cache[name] = WeerAPI()
    elif name == "thermostat":
        from devices.thermostat import ThermostatController

        _cache[name] = ThermostatController()
    elif name == "agenda":
        from scheduler.agenda import AppleCalendarMultiAccount

        _cache[name] = AppleCalendarMultiAccount()
    else:  # pragma: no cover
        raise KeyError(name)
    return _cache[name]


def _lamp(location: str = "kamer"):
    from devices.Lights import SlimmeLamp

    lamps = config.get("devices.lamps", [])
    ip = None
    for entry in lamps:
        if location.lower() in entry.get("name", "").lower():
            ip = entry.get("ip")
            break
    if ip is None and lamps:
        ip = lamps[0]["ip"]
    lamp = SlimmeLamp(config.secret("TAPO_USER"), config.secret("TAPO_PASSWORD"), ip)
    asyncio.run(lamp.connect())
    return lamp


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def zoek_internet(query):
    return websearch.search_and_summarise(query)


def start_bedtime_routine():
    from scheduler.routines import bedtime_routine

    bedtime_routine()
    log("Routine", "Bedtijd-routine gestart")
    return "Bedtijd-routine gestart"


def start_morning_routine():
    from scheduler.routines import morning_routine

    morning_routine()
    log("Routine", "Ochtend-routine gestart")
    return "Ochtend-routine gestart"


def start_party_mode(locatie="kamer"):
    lamp = _lamp(locatie)
    asyncio.run(lamp.party())
    log("Lamp", "Lamp party modus gestart")
    return "Party modus gestart"


def zet_lamp(locatie="kamer", aan=None, kleur=None, helderheid=None):
    lamp = _lamp(locatie)
    if aan is True:
        asyncio.run(lamp.aan())
        log("Lamp", f"Lamp aan ({locatie})")
        return f"Lamp aan ({locatie})"
    if aan is False:
        asyncio.run(lamp.uit())
        log("Lamp", f"Lamp uit ({locatie})")
        return f"Lamp uit ({locatie})"
    if kleur:
        asyncio.run(lamp.zet_kleur(kleur))
        log("Lamp", f"Lamp kleur: {kleur} ({locatie})")
        return f"Lamp kleur: {kleur} ({locatie})"
    if helderheid is not None:
        asyncio.run(lamp.zet_helderheid(helderheid))
        log("Lamp", f"Lamp helderheid: {helderheid}% ({locatie})")
        return f"Lamp helderheid: {helderheid}% ({locatie})"
    return "Geen geldige lampactie"


def speel_radio(zender):
    result = _get("radio").play(zender)
    log("Radio", f"Radio gestart: {zender}")
    return result


def speel_muziek(zoekterm):
    _get("spotify").speel_muziek(zoekterm)
    log("Muziek", f"Muziek gestart: {zoekterm}")
    return f"Muziek gestart: {zoekterm}"


def _audio_active():
    """Return ('spotify'|'radio'|None) for whatever is currently playing."""
    try:
        if _get("spotify").current_track().get("type") == "spotify":
            return "spotify"
    except Exception:  # noqa: BLE001
        pass
    try:
        if _get("radio").current_station().get("type") == "radio":
            return "radio"
    except Exception:  # noqa: BLE001
        pass
    return None


def stop_audio():
    which = _audio_active()
    if which == "spotify":
        _get("spotify").stop()
        return "Muziek gestopt"
    if which == "radio":
        _get("radio").stop()
        return "Radio gestopt"
    return "Geen audio actief"


def pauze_audio():
    which = _audio_active()
    if which == "spotify":
        _get("spotify").pauze()
        return "Muziek gepauzeerd"
    if which == "radio":
        _get("radio").pause()
        return "Radio gepauzeerd"
    return "Geen audio om te pauzeren"


def resume_audio():
    which = _audio_active()
    if which == "radio":
        _get("radio").resume()
        return "Radio hervat"
    _get("spotify").resume()
    return "Muziek hervat"


def pas_volume_aan(richting):
    which = _audio_active()
    delta = 10 if richting == "harder" else -10
    try:
        if which == "radio":
            player = _get("radio")
            cur = player.player.audio_get_volume()
            player.set_volume(cur + delta)
        else:
            player = _get("spotify")
            cur = player.sp.current_playback().get("device", {}).get("volume_percent", 50)
            player.set_volume(max(0, min(100, cur + delta)))
        return "Geluid harder gezet" if delta > 0 else "Geluid zachter gezet"
    except Exception as exc:  # noqa: BLE001
        return f"Kon volume niet aanpassen: {exc}"


def haal_weer_op(stad=None):
    data = _get("weer").get_all(stad or config.get("weather.city"))
    log("Weer", "Weer gegevens opgehaald")
    return data


def haal_temp_op(stad=None):
    return _get("weer").get_temp(stad or config.get("weather.city"))


def haal_wind_op(stad=None):
    return _get("weer").get_wind(stad or config.get("weather.city"))


def afspraken_vandaag():
    events = _get("agenda").return_todays_events()
    return "Afspraken voor vandaag:\n" + "\n".join(events) if events else "Geen afspraken voor vandaag."


def afspraken_morgen():
    events = _get("agenda").return_tomorrows_events()
    return "Afspraken voor morgen:\n" + "\n".join(events) if events else "Geen afspraken voor morgen."


def voeg_notitie_toe(inhoud):
    add_note(inhoud)
    return f"Notitie toegevoegd: {inhoud}"


def lees_notities():
    notes = list_all_notes()
    return "Notities:\n" + "\n".join(notes) if notes else "Geen notities gevonden."


def verzend_logs_per_mail():
    from logic.mail_sender import zip_logs_and_send

    zip_logs_and_send()
    return "Logs verzonden"


def verzend_email_op_basis_van_prompt(prompt):
    from logic.mail_sender import send_email_message

    inhoud = llm.complete(prompt, system="Schrijf een korte, nette e-mail in het Nederlands.")
    send_email_message("Automatisch bericht", inhoud)
    return "E-mail verzonden"


# --------------------------------------------------------------------------- #
# Tool schema
# --------------------------------------------------------------------------- #
functions = [
    {"name": "start_bedtime_routine", "description": "Start de bedtijd-routine met lichten uit",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "start_morning_routine", "description": "Start de ochtend-routine met lampen aan en favoriete radio.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "zet_lamp", "description": "Zet een lamp aan, uit, verander kleur of helderheid.",
     "parameters": {"type": "object", "properties": {
         "locatie": {"type": "string", "description": "De locatie van de lamp, zoals 'bureau' of 'kamer'."},
         "aan": {"type": "boolean", "description": "True om de lamp aan te zetten, False om uit te zetten."},
         "kleur": {"type": "string", "description": "De kleur van de lamp.",
                   "enum": ["rood", "oranje", "geel", "groen", "blauw", "paars", "roze", "wit"]},
         "helderheid": {"type": "integer", "description": "Helderheid in procenten (0-100)."}}}},
    {"name": "start_party_mode", "description": "Activeert party-modus op de lampen (flitsende kleuren).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "speel_radio", "description": "Start een bekende radiozender.",
     "parameters": {"type": "object", "properties": {"zender": {"type": "string", "enum": [
         "radio538", "qmusic", "nporadio2", "skyradio", "npo3fm", "slam", "radio10", "radio1", "100nl", "veronica"]}}}},
    {"name": "speel_muziek", "description": "Speel muziek via Spotify op basis van een zoekopdracht.",
     "parameters": {"type": "object", "properties": {"zoekterm": {"type": "string", "description": "De naam of zoekopdracht van het liedje."}}}},
    {"name": "stop_audio", "description": "Stop muziek of radio.", "parameters": {"type": "object", "properties": {}}},
    {"name": "pauze_audio", "description": "Pauzeer muziek of radio.", "parameters": {"type": "object", "properties": {}}},
    {"name": "resume_audio", "description": "Hervat muziek of radio.", "parameters": {"type": "object", "properties": {}}},
    {"name": "pas_volume_aan", "description": "Pas het geluidsvolume aan.",
     "parameters": {"type": "object", "properties": {"richting": {"type": "string", "enum": ["harder", "zachter"]}}}},
    {"name": "afspraken_vandaag", "description": "Geeft een overzicht van afspraken vandaag.", "parameters": {"type": "object", "properties": {}}},
    {"name": "afspraken_morgen", "description": "Geeft een overzicht van afspraken morgen.", "parameters": {"type": "object", "properties": {}}},
    {"name": "voeg_notitie_toe", "description": "Voegt een notitie toe.",
     "parameters": {"type": "object", "properties": {"inhoud": {"type": "string", "description": "De tekst van de notitie."}}}},
    {"name": "lees_notities", "description": "Leest alle opgeslagen notities.", "parameters": {"type": "object", "properties": {}}},
    {"name": "verzend_logs_per_mail", "description": "Verstuurt de logs per mail.", "parameters": {"type": "object", "properties": {}}},
    {"name": "verzend_email_op_basis_van_prompt", "description": "Maakt en verzendt een e-mail op basis van de tekst van de gebruiker.",
     "parameters": {"type": "object", "properties": {"prompt": {"type": "string", "description": "Wat de gebruiker zegt om de e-mailinhoud van te maken."}}}},
    {"name": "haal_weer_op", "description": "Geeft een volledig weerbericht.",
     "parameters": {"type": "object", "properties": {"stad": {"type": "string", "description": "De naam van de stad."}}}},
    {"name": "haal_temp_op", "description": "Geeft de huidige temperatuur.",
     "parameters": {"type": "object", "properties": {"stad": {"type": "string", "description": "De naam van de stad."}}}},
    {"name": "haal_wind_op", "description": "Geeft de huidige windsnelheid.",
     "parameters": {"type": "object", "properties": {"stad": {"type": "string", "description": "De naam van de stad."}}}},
    {"name": "zoek_internet",
     "description": "LAATSTE REDMIDDEL: alleen gebruiken als geen andere functie past en er echt actuele externe info nodig is (nieuws, openingstijden e.d.). NIET voor weer, tijd, agenda, lampen of muziek.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Waar de gebruiker naar wil zoeken."}}}},
]

functies_dispatcher = {
    "zet_lamp": zet_lamp,
    "start_morning_routine": start_morning_routine,
    "start_bedtime_routine": start_bedtime_routine,
    "speel_radio": speel_radio,
    "speel_muziek": speel_muziek,
    "stop_audio": stop_audio,
    "pauze_audio": pauze_audio,
    "resume_audio": resume_audio,
    "pas_volume_aan": pas_volume_aan,
    "afspraken_vandaag": afspraken_vandaag,
    "afspraken_morgen": afspraken_morgen,
    "voeg_notitie_toe": voeg_notitie_toe,
    "lees_notities": lees_notities,
    "verzend_logs_per_mail": verzend_logs_per_mail,
    "verzend_email_op_basis_van_prompt": verzend_email_op_basis_van_prompt,
    "haal_weer_op": haal_weer_op,
    "haal_temp_op": haal_temp_op,
    "haal_wind_op": haal_wind_op,
    "start_party_mode": start_party_mode,
    "zoek_internet": zoek_internet,
}


def _system_prompt() -> dict:
    return {"role": "system", "content": config.get("assistant.system_prompt")}


# Per gesprek een eigen historie + lock. De spraakassistent gebruikt "voice";
# elke chat-tab stuurt zijn eigen sid mee (zie chat_api). Zo lopen sessies niet
# door elkaar en is er geen race op één gedeelde lijst.
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_SESSION_TTL = 24 * 3600


def _session(key: str) -> dict:
    key = key or "voice"
    now = time.time()
    with _sessions_lock:
        s = _sessions.get(key)
        if s is None:
            s = {"history": [_system_prompt()], "lock": threading.Lock(), "seen": now}
            _sessions[key] = s
        else:
            s["seen"] = now
        for old in [k for k, v in _sessions.items() if k != key and now - v["seen"] > _SESSION_TTL]:
            _sessions.pop(old, None)
        return s


def history(session: str = "voice") -> list:
    """De levende berichtenlijst van een sessie (gebruikt door tests)."""
    return _session(session)["history"]


def reset_session(session: str = "voice") -> None:
    with _sessions_lock:
        _sessions.pop(session or "voice", None)


def _trim(hist: list) -> None:
    limit = int(config.get("ai.max_history", 20))
    if len(hist) > limit + 1:
        del hist[1:len(hist) - limit]


_MAX_TOOLS_PER_TURN = 4


def _dispatch(call) -> str:
    """Eén tool draaien; een fout wordt een nette string i.p.v. een traceback."""
    name = call.get("name", "?")
    fn = functies_dispatcher.get(name)
    if not fn:
        return f"(onbekende functie '{name}')"
    try:
        uitkomst = fn(**(call.get("arguments") or {}))
        log("AI", f"Functie {name} -> {uitkomst}")
        return str(uitkomst)
    except Exception as exc:  # noqa: BLE001
        log("ERROR", f"Functie {name} faalde: {exc}")
        return f"(kon '{name}' niet uitvoeren: {exc})"


def _run_tools(calls) -> list[tuple[str, str]]:
    """Elke unieke functie-aanroep één keer draaien (kleine modellen herhalen
    soms dezelfde call — vervelend bij o.a. e-mail versturen)."""
    out: list[tuple[str, str]] = []
    seen: set = set()
    for c in calls or []:
        key = (c.get("name"), json.dumps(c.get("arguments") or {}, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        out.append((c.get("name", "?"), _dispatch(c)))
        if len(out) >= _MAX_TOOLS_PER_TURN:
            break
    return out


def _phrase_prompt(results: list[tuple[str, str]]) -> dict:
    body = "\n".join(f"- {n}: {r}" for n, r in results)
    return {"role": "user", "content": (
        "Resultaten van de zojuist uitgevoerde functies:\n" + body +
        "\n\nGeef hiermee één kort, natuurlijk antwoord in het Nederlands. "
        "Geen opsomming, geen functienamen noemen."
    )}


def verwerk_input(text: str, session: str = "voice") -> str:
    s = _session(session)
    with s["lock"]:
        hist = s["history"]
        try:
            hist.append({"role": "user", "content": text})
            _trim(hist)
            result = llm.chat(hist, tools=functions)
            calls = result.get("tool_calls") or []
            if not calls:
                answer = result.get("content") or "Ik heb daar geen antwoord op."
                hist.append({"role": "assistant", "content": answer})
                return answer

            results = _run_tools(calls)
            answer = ""
            try:  # één natuurlijke afronding; lukt dat niet, geef de rauwe uitkomst
                followup = llm.chat(hist + [_phrase_prompt(results)])
                answer = (followup.get("content") or "").strip()
            except Exception:  # noqa: BLE001
                pass
            answer = answer or "  ".join(r for _, r in results)
            hist.append({"role": "assistant", "content": answer})
            return answer
        except Exception as exc:  # noqa: BLE001
            log("ERROR", f"Fout bij verwerken input: {exc}")
            return f"Fout bij verwerken input: {exc}"


def verwerk_input_stream(text: str, session: str = "voice"):
    """Generator van tekst-stukjes. Streamt eerst het model; kiest het een of
    meer functies, dan worden die uitgevoerd en volgt één natuurlijke afronding
    (ook gestreamd). Anders komt het antwoord meteen token voor token."""
    s = _session(session)
    with s["lock"]:
        hist = s["history"]
        hist.append({"role": "user", "content": text})
        _trim(hist)
        answer = ""
        try:
            calls = None
            for ev in llm.chat_stream(hist, tools=functions):
                if ev["type"] == "chunk":
                    answer += ev["text"]
                    yield ev["text"]
                elif ev["type"] == "tool_calls":
                    calls = ev["calls"]
                    break
                elif ev["type"] == "done":
                    if not answer and ev.get("content"):
                        answer = ev["content"]
                        yield answer

            if calls:
                results = _run_tools(calls)
                phrased = ""
                try:
                    for ev in llm.chat_stream(hist + [_phrase_prompt(results)]):
                        if ev["type"] == "chunk":
                            phrased += ev["text"]
                            yield ev["text"]
                        elif ev["type"] == "done" and not phrased and ev.get("content"):
                            phrased = ev["content"]
                            yield phrased
                except Exception:  # noqa: BLE001
                    pass
                answer = phrased.strip() or "  ".join(r for _, r in results)
                if not phrased:
                    yield answer
        except Exception as exc:  # noqa: BLE001
            log("ERROR", f"Fout bij streamen: {exc}")
            yield f"\n[fout: {exc}]"
            return
        if answer:
            hist.append({"role": "assistant", "content": answer})
