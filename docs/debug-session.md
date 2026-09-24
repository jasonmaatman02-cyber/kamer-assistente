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

#### S2-3 Camera/presence: backoff, bevroren frame, CPU, herstel-grace, log-spam
Reproductie lokaal met een gescripte nep-`cv2` (`tests/test_camera.py`, 12 tests, 10 falen tegen de oude code, 5/5 stabiel op de nieuwe).
- **Backoff ontbrak**: een ontbrekende/losgetrokken USB-camera werd door presence (elke ~3s `keep_alive` -> `_ensure_running`) elke tick opnieuw geopend (V4L2-waarschuwing per poging, CPU). Nu: 1e mislukking direct opnieuw (USB-hikje), daarna 5/10/20/40/max 60s; een echt frame reset de teller.
- **Bevroren frame**: `_latest` bleef na een vastgelopen `read()` het laatste frame 'vers' tonen -> een bevroren beeld met persoon = kamer voor altijd bezet (lamp gaat nooit uit). Frames dragen nu een tijdstempel; presence vraagt `latest_jpeg(max_age_s=max(10, 4*interval))`, ouder = sensor ONBEKEND (bestaande hold-logica). `frame_age_s` zichtbaar in `/api/health` en `/api/camera_status`.
- **CPU (gemeten)**: de capture-thread draaide op de volle `camera.fps` (resize+JPEG-encode per frame) terwijl presence er maar 1 per 3s gebruikt. Nu: als alleen presence meekijkt (`_viewers <= 1`, geen snapshot in 30s) een frame per `presence.interval_s/2` (0.5-2s), met `Event`-wake zodat een nieuwe browser-kijker meteen de volle framerate krijgt en `release()` niet de hele slaap uitwacht. Dev-meting: 3.07 ms/frame -> 4.6% vs 0.2% van 1 core bij 15 vs 0.67 fps (Pi ~5-10x meer).
- **HOG is de echte CPU-verbruiker**: dev-meting 640x360 ~190-270 ms/frame; met verkleining 0.6x ~20 ms (~10x). Nieuwe **opt-in** instelling `presence.detect_scale` (default 1.0 = ongewijzigd gedrag, want detectie-nauwkeurigheid op het echte beeld moet live getoetst worden) + `detect_ms` in `/api/presence` om dat op de Pi te meten.
- **Herstel-grace**: na een lange camerastoring maakte 1 negatief frame de kamer direct EMPTY (grace liep vanaf vóór de storing). Grace start nu bij herstel van de sensor.
- **Log-spam**: identieke onverwachte worker-fout werd elke 3s gelogd (~29k regels/dag); nu 1x per 10 min per unieke fout.
- **Bestanden**: `Dashboard/backend/camera_api.py`, `presence.py`, `system_api.py`, `config/settings.py`, `Dashboard/static/scripts/settings.js`, `tests/test_camera.py`, `tests/test_units.py` (2 camera-fakes accepteren nu `max_age_s`).
- **Resterend risico / niet testbaar zonder hardware**: echt USB-gedrag (device-index verandert na replug, `read()` dat langer dan de OpenCV-select-timeout blokkeert); HOG kan een zittend/liggend persoon missen (bekende beperking van de HOG-mensdetector); of `detect_scale=0.6` op het echte beeld genoeg detecteert. Bewust niet gedaan: `time.monotonic()` voor de grace-klok (NTP-sprong bij boot op een Pi zonder RTC is eenmalig en onschadelijk; zou 3 tests op een klok-seam laten herschrijven).

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
