# Kamer-AI debug- en stabilisatiesessie

Doorlopend logboek. Formaat per item: probleem, oorzaak, oplossing, bestanden,
tests, resultaat, resterend risico. Nieuwste sessie bovenaan.

## Sessie 2 (2026-09-24, Pi tijdelijk onbereikbaar -> alles lokaal getest)

Omgeving: Windows 11 dev-machine, Python 3.12. Geen SSH/deploy mogelijk;
wijzigingen worden gecommit en gepusht, **deploy naar de Pi volgt zodra die
weer bereikbaar is** (`bash deploy/update-pi.sh` + `pip install -r
requirements-dashboard.txt` voor de nieuwe `zeroconf`-dependency).

### Nog niet op de Pi gedeployed
- 321c094 mDNS-discovery van de Pi in `/api/devices` (nieuwe dependency zeroconf)

### Items

#### S2-1 TTS: argument-injectie, overlappende spraak, hangende afspeellus, temp-lek
- **Probleem**: (a) `espeak-ng` kreeg LLM-/notitie-/routinetekst als losse argv; tekst die met `-` begint werd als optie geparsed (`-w<pad>` overschrijft een bestand, `-f<pad>` leest een bestand). (b) `speak()` niet geserialiseerd: `pygame.mixer.music` is een globaal kanaal, een wekker tijdens een routine haalde elkaars afspelen onderuit; Piper-stem (~60MB) werd bij gelijktijdige eerste aanroepen meerdere keren geladen. (c) `while music.get_busy()` zonder timeout: device dat verdwijnt = thread hangt voor altijd. (d) `synthesize()` liet het `mkstemp`-bestand liggen bij mislukking.
- **Oorzaak**: geen `--`, geen lock, geen deadline, geen cleanup in het foutpad.
- **Fix**: `--` voor de tekst; `_speak_lock` rond `speak()`; `_piper_lock` (dubbel-gecontroleerd) rond het laden; 180s deadline in `_play` (`music.stop()` bij overschrijding); temp-bestand opruimen bij mislukking; `pygame.time.Clock().tick` vervangen door `time.sleep(0.1)`.
- **Bestanden**: `ai/tts.py`, `tests/test_tts.py`.
- **Tests**: 5 nieuwe tests; alle 5 falen tegen de oude code (o.a. Piper 5x geladen, 5 gelijktijdige afspeelbeurten), slagen nu. Volledige suite groen.
- **Resterend risico**: `_play_system` (aplay/ffplay) valt buiten de lock-garantie niet; espeak-`lang` komt uit config (alleen via wachtwoord-beschermde Settings).

#### S2-2 Opruiming: 3 ongebruikte imports
`logic/logger.py` (os), `main.py` (config), `voice/Whisper_short.py` (config). ruff F-klasse nu schoon.

#### Statische analyse (uitgevoerd, geen verdere bevindingen)
- ruff F: schoon na S2-2. bandit: 0 High, 1 Medium (`0.0.0.0` bind in `rundashboard.py`, bewust: LAN-dashboard achter optioneel wachtwoord), 21 Low (vaste-argv-subprocess, `try/except/pass`; beoordeeld, alleen tts-argv was echt).
- vulture: `devices/Lights.py:65` ongebruikte parameters `stappen`/`vertraging` (`zet_helderheid`) -- API-compat, laten staan.
- git-historie: nooit een `.env`/token/settings.json gecommit; `data/notes.json` is `{}`.
- pip-audit: **niet uitvoerbaar** (geen verbinding met de kwetsbaarhedendatabase vanaf deze machine).

## Sessie 1 (2026-09-14/15, live op de Pi) -- samenvatting

Alle items hieronder zijn getest, gedeployed en live geverifieerd.

| # | Probleem | Oorzaak | Fix |
|---|----------|---------|-----|
| 1 | AI-chat timeout na 120s | waitress `channel_timeout` + chat.js-timer 120s; Ollama koude start ~4 min | beide naar 300s |
| 2 | Steeds terugkerende trage AI | Ollama `OLLAMA_KEEP_ALIVE` default 5 min | drop-in override 24h via setup-pi.sh |
| 3 | TTS-subprocessen zonder timeout | `subprocess.run` zonder `timeout=` | timeouts + tests |
| 4 | Race in `AlarmScheduler.set_alarm/cancel` | check-then-act zonder lock | lock |
| 5 | Auth-tokens lekten geheugen | alleen eigen token opgeruimd | sweep van alle verlopen tokens |
| 6 | `notes.py` lost-update race | load-modify-save zonder lock | lock |
| 7 | `routines_api.py` lost-update race | idem | lock |
| 8 | `camera.detect_threshold` werd nooit gebruikt server-side | niet doorgegeven aan `count_people` | doorgegeven; waarschijnlijkste oorzaak "lamp gaat niet uit" |
| 9 | Twee losse `AlarmScheduler`-instanties (dashboard vs spraak) | twee singletons | een gedeeld object |
| 10 | `RadioPlayer.play()` blokkeerde altijd 4s | blinde `sleep(2)` x2 | adaptief pollen |
| 11 | `SpotifyOAuth` zonder timeout | `requests_timeout` niet gezet op auth-manager | 10s |
| 12 | Gebroken AI-stream plakte 2e antwoord achter partieel antwoord | fallback-`chat()` na gedeeltelijke stream | alleen fallback als nog niets gestreamd |
| 13 | `settings.json` niet-atomisch geschreven | `write_text` (truncate) | tmp + `os.replace` |
| 14 | Google Calendar-client zonder HTTP-timeout | `build(credentials=)` maakt httplib2 zonder timeout | eigen `AuthorizedHttp(timeout=15)` |
| 15 | Cache-stampede in `_cached()` | geen lock rond check-produce-store | dubbel-gecontroleerde per-key lock |
| 16 | `google_calendar_token.json` niet-atomisch | `write_text` | tmp + `os.replace` (2 plekken) |
| 17 | `active_device_id()` koos willekeurig eerste Spotify-device | fallback `devices[0]` | actief -> Pi (naam+type) -> None |
| 18 | Spraak/AI-`speel_muziek` gebruikte die logica niet | apart pad zonder device_id | gedeelde `services.start_playback` |
| 19 | `/api/calendar/events` zonder cache | geen `_cached` | cache per datumbereik |
| 20 | Rate-limit bypass in `auth.py` | check-then-increment zonder lock | `_auth_lock` |
| 21 | Pi niet zichtbaar in dashboard-Spotify-lijst | Web API kent ongekoppeld zeroconf-device niet | lokale mDNS-discovery (321c094) |

Bekende, bewust niet gefixte P3's uit sessie 1: race op gedeeld Tapo-
verbindingsobject tussen presence en handmatige bediening; calendar.js zonder
in-flight-guard; TZID/offset wordt genegeerd in agenda-normalisatie (bewuste
"geen ICS-library"-keuze); OpenVPN-**server** draait op de Pi (buiten dit
project, wel gemeld).
