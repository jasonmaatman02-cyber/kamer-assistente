"""Natural-language command handler.

The user's text goes to the LLM (:mod:`ai.llm`, Ollama by default) with a set of
tool definitions. If the model picks a tool we run it and return its result;
otherwise we return the model's plain reply.
"""
from __future__ import annotations

import asyncio
import inspect
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
        from sound_system.radio import shared_player

        _cache[name] = shared_player()
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

    lamps = [e for e in (config.get("devices.lamps", []) or []) if isinstance(e, dict)]
    ip = None
    for entry in lamps:
        if str(location or "").lower() in str(entry.get("name", "")).lower():
            ip = entry.get("ip")
            break
    if not ip and lamps:
        ip = lamps[0].get("ip")
    if not ip:
        raise RuntimeError("Geen lamp geconfigureerd -- voeg er een toe bij Settings > Lampen")
    lamp = SlimmeLamp(config.secret("TAPO_USER"), config.secret("TAPO_PASSWORD"), ip)
    asyncio.run(lamp.connect())
    return lamp


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def zoek_internet(query):
    return websearch.search_and_summarise(query)


def _failures_suffix(failures) -> str:
    """Zodat het model (en dus de gebruiker) hoort dat een stap niet lukte i.p.v. 'gestart'."""
    return f" (maar niet alles lukte: {'; '.join(failures)})" if failures else ""


def start_bedtime_routine():
    from scheduler.routines import bedtime_routine

    failures = bedtime_routine()
    log("Routine", "Bedtijd-routine gestart")
    return "Bedtijd-routine gestart" + _failures_suffix(failures)


def start_morning_routine():
    from scheduler.routines import morning_routine

    failures = morning_routine()
    log("Routine", "Ochtend-routine gestart")
    return "Ochtend-routine gestart" + _failures_suffix(failures)


def start_party_mode(locatie="kamer"):
    lamp = _lamp(locatie)
    asyncio.run(lamp.party())
    log("Lamp", "Lamp party modus gestart")
    return "Party modus gestart"


def zet_lamp(locatie="kamer", aan=None, kleur=None, helderheid=None):
    if aan is None and not kleur and helderheid is not None:
        try:
            helderheid = max(1, min(100, int(helderheid)))     # de Tapo weigert 0 en >100
        except (TypeError, ValueError):
            return "Geen geldige helderheid (verwacht 1-100)"
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
    player = _get("radio")
    result = player.play(zender)
    if getattr(player, "last_error", None):
        log("Radio", f"Radio starten mislukt ({zender}): {player.last_error}")
    else:
        log("Radio", f"Radio gestart: {zender}")
    return result


def _spotify_reason(dj) -> str:
    return getattr(dj, "last_error", None) or "onbekende fout"


def speel_muziek(zoekterm):
    dj = _get("spotify")
    if not dj.speel_muziek(zoekterm):
        # voorheen altijd "Muziek gestart", ook als Spotify onbereikbaar was of er niets
        # gevonden werd -- het model meldde de gebruiker dan een succes dat er niet was
        log("Muziek", f"Muziek starten mislukt: {zoekterm} ({_spotify_reason(dj)})")
        return f"Kon '{zoekterm}' niet afspelen: {_spotify_reason(dj)}"
    log("Muziek", f"Muziek gestart: {zoekterm}")
    return f"Muziek gestart: {zoekterm}"


def _audio_active():
    """Return ('spotify'|'radio'|None) for whatever is currently playing."""
    try:
        if _get("spotify").current_track().get("type") == "spotify":
            return "spotify"
    except Exception as exc:  # noqa: BLE001 - dienst even weg is normaal
        print(f"[gpt] _audio_active/spotify: {exc!r}")
    try:
        if _get("radio").current_station().get("type") == "radio":
            return "radio"
    except Exception as exc:  # noqa: BLE001
        print(f"[gpt] _audio_active/radio: {exc!r}")
    return None


def stop_audio():
    which = _audio_active()
    if which == "spotify":
        dj = _get("spotify")
        return "Muziek gestopt" if dj.stop() else f"Kon de muziek niet stoppen: {_spotify_reason(dj)}"
    if which == "radio":
        _get("radio").stop()
        return "Radio gestopt"
    return "Geen audio actief"


def pauze_audio():
    which = _audio_active()
    if which == "spotify":
        dj = _get("spotify")
        return "Muziek gepauzeerd" if dj.pauze() else f"Kon de muziek niet pauzeren: {_spotify_reason(dj)}"
    if which == "radio":
        _get("radio").pause()
        return "Radio gepauzeerd"
    return "Geen audio om te pauzeren"


def resume_audio():
    which = _audio_active()
    if which == "radio":
        _get("radio").resume()
        return "Radio hervat"
    dj = _get("spotify")
    return "Muziek hervat" if dj.resume() else f"Kon de muziek niet hervatten: {_spotify_reason(dj)}"


def pas_volume_aan(richting):
    which = _audio_active()
    delta = 10 if richting == "harder" else -10
    try:
        if which == "radio":
            player = _get("radio")
            cur = player.player.audio_get_volume()
            ok = player.set_volume(cur + delta)
        else:
            player = _get("spotify")
            playback = player.sp.current_playback() or {}      # None als er niets speelt
            cur = (playback.get("device") or {}).get("volume_percent", 50)
            ok = player.set_volume(max(0, min(100, cur + delta)))
        if not ok:
            reason = getattr(player, "last_error", None)
            return f"Kon het volume niet aanpassen{': ' + reason if reason else ''}"
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


def _afspraken(cal, events, dag: str) -> str:
    """Tekst voor de AI. Een agenda die niet kon worden opgehaald (Google weg, token verlopen) is GEEN lege
    dag: ``return_*_events()`` geeft dan ook "Geen events ...", en het model zei dan gerust "je hebt niets
    gepland" terwijl het niet wist. De fout wordt erbij gemeld (het dashboard doet dat al)."""
    real = [e for e in events if not str(e).startswith("Geen events")]
    errors = list(getattr(cal, "fetch_errors", None) or [])
    if errors and not real:
        return f"Ik kon de agenda niet ophalen ({'; '.join(errors)}), dus ik weet niet wat er {dag} gepland staat."
    if not real:
        return f"Geen afspraken voor {dag}."
    note = f"\n(Let op, niet alles kon worden opgehaald: {'; '.join(errors)})" if errors else ""
    return f"Afspraken voor {dag}:\n" + "\n".join(real) + note


def afspraken_vandaag():
    cal = _get("agenda")
    return _afspraken(cal, cal.return_todays_events(), "vandaag")


def afspraken_morgen():
    cal = _get("agenda")
    return _afspraken(cal, cal.return_tomorrows_events(), "morgen")


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
# sid komt van de client (chat-tab-id): zonder bovengrens kon één client met steeds nieuwe
# sid's binnen de TTL onbeperkt sessies (elk met eigen historie) laten aanmaken.
_MAX_SESSIONS = 200


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
        if len(_sessions) > _MAX_SESSIONS:
            # de minst recent gebruikte weg (niet de huidige, en niet eentje die net een beurt draait)
            idle = sorted((v["seen"], k) for k, v in _sessions.items() if k != key and not v["lock"].locked())
            for _seen, k in idle[: len(_sessions) - _MAX_SESSIONS]:
                _sessions.pop(k, None)
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

# Begrenzing van gelijktijdige AI-beurten. Elke beurt (chat, spraak, /api/
# send_message) houdt een waitress-worker vast voor de hele -- op een Pi 4B
# soms minutenlange -- LLM-aanroep, en Ollama verwerkt er toch maar één tegelijk.
# Zonder limiet zette een reeks chat-verzoeken (meerdere tabs, of opnieuw
# versturen na een browser-timeout terwijl de server nog bezig is) alle 16
# workers vast en bevroor het hele dashboard (lokaal gereproduceerd:
# tools/stress_dashboard.py ollama --tabs 18 -> 5/8 canary-timeouts).
_LLM_SLOT_COUNT = 2
_llm_slots = threading.BoundedSemaphore(_LLM_SLOT_COUNT)
_slots_full_since: float | None = None
_SLOTS_STUCK_S = 1500.0       # alle slots zo lang bezet = een slot is nooit teruggegeven (lek)
_SESSION_WAIT_S = 0.5
_SLOT_WAIT_S = 0.5
_BUSY_SESSION = "Ik ben nog bezig met je vorige vraag -- even geduld."
_BUSY_GLOBAL = "De assistent is nu druk bezig met andere verzoeken. Probeer het over een minuutje opnieuw."


def liveness(now: float | None = None) -> tuple[bool, str]:
    """(gezond, uitleg) voor de watchdog. Elke beurt geeft zijn slot in een ``finally`` terug, dus
    alle slots langdurig bezet betekent een lek: daarna zegt de assistent voor altijd "druk bezig".
    Een echte beurt duurt hooguit de LLM-timeout (330 s) plus tools, ver onder de grens."""
    global _slots_full_since
    t = now if now is not None else time.monotonic()
    free = getattr(_llm_slots, "_value", _LLM_SLOT_COUNT)
    if free > 0:
        _slots_full_since = None
        return True, f"{free}/{_LLM_SLOT_COUNT} AI-slots vrij"
    if _slots_full_since is None:
        _slots_full_since = t
    age = t - _slots_full_since
    if age > _SLOTS_STUCK_S:
        return False, f"alle AI-slots al {int(age)}s bezet (grens {int(_SLOTS_STUCK_S)}s)"
    return True, f"alle AI-slots bezet sinds {int(age)}s"


def _begin_turn(s):
    """(release, None) als deze beurt mag draaien, anders (None, melding).
    Wacht nooit langer dan een paar seconden op de sessie-lock of een slot."""
    if not s["lock"].acquire(timeout=_SESSION_WAIT_S):
        return None, _BUSY_SESSION
    if not _llm_slots.acquire(timeout=_SLOT_WAIT_S):
        s["lock"].release()
        return None, _BUSY_GLOBAL

    def release():
        _llm_slots.release()
        s["lock"].release()

    return release, None


friendly_ai_error = llm.friendly_error      # bewaard voor bestaande aanroepers/tests


# Een tool-uitkomst gaat als tekst terug de prompt in; onbegrensd (een lange notitielijst, een
# webresultaat) rekt dat de context van een klein model op een Pi tot minutenlange antwoorden.
_MAX_TOOL_RESULT_CHARS = 4000


def _tool_kwargs(name: str, fn, raw) -> dict:
    """De argumenten die het model meegaf, geschikt voor ``fn``. Kleine modellen verzinnen parameters
    (logboek 2026-09-14: ``verzend_logs_per_mail() got an unexpected keyword argument 'prompt'``) of
    geven geen object mee; een onbekende parameter weggooien is veiliger dan de hele tool laten falen,
    en een ontbrekende verplichte parameter geeft nog steeds een duidelijke foutmelding."""
    if not isinstance(raw, dict):
        return {}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return dict(raw)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(raw)
    kwargs = {k: v for k, v in raw.items() if k in params}
    dropped = sorted(set(raw) - set(kwargs))
    if dropped:
        log("AI", f"Functie {name}: onbekende parameters van het model genegeerd: {', '.join(map(str, dropped))}")
    return kwargs


def _dispatch(call) -> str:
    """Eén tool draaien; een fout wordt een nette string i.p.v. een traceback."""
    name = call.get("name", "?")
    fn = functies_dispatcher.get(name)
    if not fn:
        return f"(onbekende functie '{name}')"
    try:
        uitkomst = fn(**_tool_kwargs(name, fn, call.get("arguments")))
        log("AI", f"Functie {name} -> {uitkomst}")
        text = str(uitkomst)
        if len(text) > _MAX_TOOL_RESULT_CHARS:
            text = text[:_MAX_TOOL_RESULT_CHARS].rstrip() + " ... (ingekort)"
        return text
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


def _drop_last_user(hist: list, text: str) -> None:
    if hist and hist[-1].get("role") == "user" and hist[-1].get("content") == text:
        hist.pop()


def verwerk_input(text: str, session: str = "voice") -> str:
    s = _session(session)
    release, busy = _begin_turn(s)
    if busy:
        return busy
    try:
        hist = s["history"]
        try:
            hist.append({"role": "user", "content": text})
            _trim(hist)
            result = llm.chat(hist, tools=functions)
            if result.get("error"):
                # geen foutmelding als 'antwoord' in de geschiedenis (het model zou 'm daarna als eigen
                # tekst zien) en de mislukte vraag weer weg, zodat een nieuwe poging niet dubbel staat
                _drop_last_user(hist, text)
                return f"Fout bij verwerken input: {result['error']}"
            calls = result.get("tool_calls") or []
            if not calls:
                answer = result.get("content") or "Ik heb daar geen antwoord op."
                hist.append({"role": "assistant", "content": answer})
                return answer

            results = _run_tools(calls)
            answer = ""
            try:  # één natuurlijke afronding; lukt dat niet, geef de rauwe uitkomst
                followup = llm.chat(hist + [_phrase_prompt(results)])
                answer = "" if followup.get("error") else (followup.get("content") or "").strip()
            except Exception as exc:  # noqa: BLE001
                log("ERROR", f"Afronding na tool-call mislukt: {exc}")
            answer = answer or "  ".join(r for _, r in results)
            hist.append({"role": "assistant", "content": answer})
            return answer
        except Exception as exc:  # noqa: BLE001
            log("ERROR", f"Fout bij verwerken input: {exc}")
            _drop_last_user(hist, text)
            return f"Fout bij verwerken input: {friendly_ai_error(exc)}"
    finally:
        release()


def verwerk_input_stream(text: str, session: str = "voice"):
    """Generator van tekst-stukjes. Streamt eerst het model; kiest het een of
    meer functies, dan worden die uitgevoerd en volgt één natuurlijke afronding
    (ook gestreamd). Anders komt het antwoord meteen token voor token."""
    s = _session(session)
    release, busy = _begin_turn(s)
    if busy:
        yield busy
        return
    try:
        hist = s["history"]
        hist.append({"role": "user", "content": text})
        _trim(hist)
        answer = ""
        try:
            calls = None
            for ev in llm.chat_stream(hist, tools=functions):
                if ev["type"] == "error":
                    _drop_last_user(hist, text)
                    yield f"\n[fout: {ev['message']}]"
                    return
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
                        if ev["type"] == "error":
                            break          # afronding mislukt: gewoon de tool-uitkomsten tonen
                        if ev["type"] == "chunk":
                            phrased += ev["text"]
                            yield ev["text"]
                        elif ev["type"] == "done" and not phrased and ev.get("content"):
                            phrased = ev["content"]
                            yield phrased
                except Exception as exc:  # noqa: BLE001
                    log("ERROR", f"Afronding na tool-call (stream) mislukt: {exc}")
                answer = phrased.strip() or "  ".join(r for _, r in results)
                if not phrased:
                    yield answer
        except Exception as exc:  # noqa: BLE001
            log("ERROR", f"Fout bij streamen: {exc}")
            _drop_last_user(hist, text)
            yield f"\n[fout: {friendly_ai_error(exc)}]"
            return
        if answer:
            hist.append({"role": "assistant", "content": answer})
    finally:
        release()
