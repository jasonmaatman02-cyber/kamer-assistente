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
- 81f8415 / 2b66748 camera-backoff, presence-herstel, TTS-hardening, lamp-timeout (S2-1..S2-4)
- 956db0f single-flight SWR-cache voor trage externe diensten (S2-5)
- dafa087 begrensde AI-concurrency, eindige Ollama-timeout, OpenAI-key-herlaad (S2-6)
- 84e3fcf UI-fixes (lamp-schakelaar, vriendelijke meldingen), dev-server (S2-7)
- aded680 deploy-scripts + 0600-rechten (S2-8)
- 408ccba wekker/routines + spraakpijplijn (S2-9, S2-10)
- (volgende commit) detectiefout-als-onbekend + notifications (S2-11)

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

#### S2-4 (P1) Offline lamp bevriest het HELE dashboard; Tapo zonder timeout; manual-vs-auto race
**Reproductie (lokaal, echte app onder waitress, threads=16)**: `tools/repro_offline_lamp.py` -- 2 lampen op een blackhole-adres + 2 'Devices-tabbladen' die open-loop pollen (zoals `devices.js`'s `setInterval`). Resultaat voor de fix: waitress `Task queue depth` > 47, **5 van 9 `/api/health`-requests time-outten (>8s)**, in 70s slechts 6 lamp-polls beantwoord = dashboard volledig bevroren.
- **Oorzaak**: (1) `ApiClient` zonder timeout: een onbereikbare lamp faalt pas na ~21s (gemeten; met `timeout_s=3` na 3.0s). (2) `services.lamp()` serialiseert connects per lamp achter een lock zonder wachtgrens en elke wachtende thread deed daarna zijn EIGEN 21s-poging -> polls (1 per 10s per lamp per tab) stapelen zich sneller op dan ze afgehandeld worden -> alle 16 workers bezet. (3) `devices.js` vuurde overlappende polls per kaart af.
- **Fix**: `SlimmeLamp.connect()` geeft `timeout_s` mee (`devices.lamp_timeout_s`, default 6; **integer** -- een float geeft `TypeError` in tapo en viel eerst stilzwijgend terug op geen timeout, ontdekt via een echte-bibliotheek-test); `services.lamp()`: lock-wachttijd begrensd (15s) + 2s 'net mislukt'-venster waarin wachtenden meteen falen; zelfde in `reconnect_lamp`; `devices.js`: in-flight-guard per kaart.
- **Race manual-vs-auto** (deed het nieuwe mandaat expliciet vragen): een openstaande auto-AAN-retry zette de lamp na een handmatige UIT alsnog aan; een auto-commando dat nog op de trage verbinding wachtte overschreef een intussen gedane handmatige actie. Nu: `note_manual_action()` annuleert pending retries en verhoogt `_manual_epoch`; `_auto_light` slaat zijn commando over als de epoch veranderde tijdens het verbinden.
- **Resultaat na de fix** (zelfde load): 0 health-timeouts, 48 lamp-polls direct beantwoord (503).
- **Bestanden**: `devices/Lights.py`, `Dashboard/backend/services.py`, `Dashboard/backend/presence.py`, `Dashboard/static/scripts/devices.js`, `config/settings.py`, `tests/test_camera.py` (+9 tests, o.a. echte tapo-lib tegen TEST-NET), `tests/conftest.py`, `tools/repro_offline_lamp.py`.
- **Resterend**: echte Tapo-sessie/`SESSION_TIMEOUT`-gedrag en firmware-eigenaardigheden niet testbaar zonder lamp; `/api/health` blokkeert op `_ollama_reachable()` (2s timeout; lokaal op Windows altijd 2s bij gesloten poort, op de Pi alleen bij een hangende Ollama) en `system_stats` (~200ms `cpu_percent`-sampling) -- health wordt alleen handmatig/monitoring gebruikt, niet gepolld.

#### S2-5 (P1) Trage externe dienst bevriest het hele dashboard (Spotify / weer / agenda)
**Reproductie (lokaal)**: `tools/stress_dashboard.py <scenario>` -- echte app onder waitress (16 threads), de dienst vervangen door een nep die 25s blokkeert, 3 browser-achtige tabs (open-loop `setInterval`-polls), `/api/config` als canary. Vóór de fix: canary-timeouts **7/12 (spotify), 5/20 (weer), 6/16 (agenda)**, waitress "connection limit reached", 21/6/13 polls afgehandeld.
- **Oorzaak**: aantal geblokkeerde workers = aanvraagfrequentie x doorlooptijd. `current_playing()` (elke ~4s per tab, plus /api/overview) en `/api/devices` waren ongecached; `_cached()` en `service_status()` lieten elke gelijktijdige aanroep achter een lock wachten (resp. zelf dezelfde hangende probes draaien); `fresh=True`/invalidate wierp de oude waarde weg, zodat álle volgers moesten wachten. Al bij 10s upstream-vertraging + 3 tabs is de pool leeg.
- **Fix**: `_cached()` = single-flight + stale-while-revalidate (één thread ververst, volgers krijgen direct de laatste waarde; nooit gecached = begrensd wachten (4s) dan `fallback`); `_expire()` i.p.v. wegpoppen; `current_playing()` 2s-cache + `invalidate()` na elke media-actie; `/api/devices`-logica verhuisd naar `services.spotify_device_list()` (5s-cache); `service_status()` single-flight; `_spotify_has_device()` via `_cached`. Frontend: geen overlappende/achtergrond-polls in `home.js` en `media.js`.
- **Resultaat na de fix**: canary-timeouts **0/45 in alle drie** (mediaan 0.00s); afgehandelde polls 145/44/59.
- **Bijvangst**: `_simple()` meldde pauze/hervat als succes terwijl `SpotifyDJ._call()` de fout had ingeslikt (False) -> "geen actief apparaat" bereikte de UI nooit. Nu 409 `no_device` / 502 met reden; `media.js` toont die fouten via `playAction`.
- **Bestanden**: `Dashboard/backend/services.py`, `media_api.py`, `sound_system/muziek.py`, `Dashboard/static/scripts/home.js`, `media.js`, `tests/test_swr.py` (8 nieuw), `tools/stress_dashboard.py`.
- **Resterend**: chat/LLM-aanvragen houden nog een worker vast per lopend verzoek (apart item S2-6).

#### S2-6 (P1) AI-verzoeken kunnen het hele dashboard vastzetten; Ollama zonder timeout; OpenAI-key wijzigt niet
**Reproductie**: `python tools/stress_dashboard.py ollama --hang 30 --tabs 18` (trage LLM, 18 chat-zenders): canary-timeouts **5/8**, dashboard bevroren.
- **Oorzaak**: elke chat/spraak-beurt hield een waitress-worker vast voor de hele LLM-aanroep (op een Pi minuten), zonder globale limiet; wachten op de per-sessie-lock was onbegrensd (opnieuw versturen na een browser-timeout terwijl de server nog bezig is); `ollama.Client` had `timeout=None` (een vastgelopen Ollama = worker voor altijd vast); de gecachte OpenAI-client gebruikte na een key-wijziging in Settings nog de oude key (Settings meldt "toegepast") en had de SDK-default van 10 min x 2 retries.
- **Fix**: `_llm_slots` (max 2 gelijktijdige beurten) + `_begin_turn()` met 0.5s-wachttijden en nette NL-melding ("nog bezig met je vorige vraag" / "druk bezig"); lock+slot altijd vrijgegeven (ook bij LLM-fout en bij afgebroken stream); `ai.ollama_timeout_s` (default 330s, dekt de gemeten koude start ~225s, net boven chat.js/waitress 300s); OpenAI-client wordt herbouwd bij een andere key, timeout 60s, 1 retry.
- **Resultaat**: 0/36 canary-timeouts, mediaan 0.03s onder dezelfde flood.
- **Bestanden**: `logic/gpt_handler.py`, `ai/llm.py`, `config/settings.py`, `tests/test_llm.py` (+7 tests).
- **Resterend / niet testbaar zonder Pi**: echte Ollama-koude-start; of 330s ook bij trage generatie van lange antwoorden volstaat (per chunk bij streaming).

#### S2-7 UI-controle in een echte browser (lokale, geisoleerde dev-server)
`tools/dev_server.py` (temp-config, camera/presence/TTS uit, lampen op TEST-NET, **echte geheimen uit `.env` gescrubd** -- `config` laadt `.env` bij import; mijn eerdere stress-runs hadden 17 echte sleutels in de process-omgeving) + `.claude/launch.json` (lokaal, niet gecommit).
- Alle pagina's geladen (main, devices, media, environment, routines, notes, notifications, chat, settings, calendar): geen JS-excepties; enige console-fouten zijn de verwachte 503's voor niet-geconfigureerd Spotify. Foutstaten renderen netjes: Media "Spotify niet verbonden", Devices beide lampen "offline" (geen freeze), Chat toont de Ollama-fout met `ollama pull`-hint, nieuw `detect_scale`-veld staat in Settings.
- **Bug**: lamp-aan/uit-schakelaar op Devices had geen zichtbaar spoor: `.toggle` is een `<label>` (inline) waardoor `width/height` genegeerd werden buiten een flex-container (gemeten 4px breed). Fix `display:inline-block` in `main.css`; na de fix 46x24px (DOM-meting + screenshot).
- **UX**: Media toonde spotipy's kale "No client_id. Pass it or set a SPOTIPY_CLIENT_ID..." -> nu Nederlandse uitleg + verwijzing naar Settings; Kalender-tab toonde zonder gekoppeld account een lege kalender zonder uitleg -> nu "Geen agenda gekoppeld".
- **Niet gefixt (cosmetisch)**: `chat.js` reset naar een hardcoded "Hey Jason!"-begroeting; `config.DEFAULTS` bevat hardcoded voorbeeld-lamp-IP's (192.168.2.15 / 192.168.3.19).
- **Bestanden**: `Dashboard/static/styles/main.css`, `services.py`, `tests/test_calendar_api.py`, `tests/test_swr.py`, `tools/dev_server.py`, `tools/*.py` (scrub).

#### S2-8 Deploy-scripts en bestandsrechten
- `shellcheck` op `setup-pi.sh`/`update-pi.sh`: schoon (nu ook als test in `tests/test_deploy.py`, skip als shellcheck ontbreekt).
- **Bug (setup)**: de Ollama-drop-in werd geactiveerd op `systemctl list-unit-files ollama.service` -- exit-status is niet op elke systemd-versie betrouwbaar; bij niet-geinstalleerde Ollama zou `systemctl restart ollama` de hele setup afbreken (`set -e`) vóór de dashboard-service. Nu `unit_exists()` (`--no-legend`, leeg = bestaat niet) + `command -v ollama`-guard; getest met een gestubde `systemctl`.
- **Verbetering (update)**: `update-pi.sh` herstartte maar controleerde niet of het dashboard terugkwam. Nu health-check (`/api/config`, max 40s) met logregels + exacte rollback-opdracht (`git reset --hard <vorige>`) en exit 1. Bewust geen automatische rollback (zou lokale wijzigingen kunnen wissen).
- **Rechten (regressie van S1-13/S1-16)**: de atomische tmp+replace-writes gaven `google_calendar_token.json` (refresh-token!) de default 0644 terug; `config.set_secret` schreef `.env` zonder 0600. Nu `chmod 0600` op `.env` en beide token-schrijfplekken (no-op op Windows).
- **Niet uitgevoerd / advies**: extra systemd-hardening (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem`) -- risico op kapotte toegang tot /dev/video0/ALSA, niet testbaar zonder Pi; `MemoryMax=1200M` en `Restart=always`/`RestartSec=3` beoordeeld: prima (dashboard ~230MB RSS).

#### S2-9 Wekker/routines: blokkerende set_alarm, gedeelde callback, her-armen, dubbele runs
Reproductie: `tests/test_alarm.py` (10 tests; de blokkeer-test faalt tegen de oude code met "set_alarm blokkeert achter de lopende routine").
- **Blokkade**: de wekker-callback (= een hele routine: LLM-groet tot 330s, TTS, radio) draait IN de wekker-thread; `set_alarm()` deed daar `join()` zonder timeout op, onder de scheduler-lock. Een POST/DELETE `/api/alarm` tijdens een lopende routine hing dus minuten en pinde een waitress-thread per klik (16 in totaal). Nu: elke wachter-thread heeft een eigen stop-event + eigen tijdstip, geen join op een afgegane thread, cancel joint buiten de lock met 0,5s-timeout en nooit op de eigen thread (`set_alarm` vanuit de callback gaf `RuntimeError: cannot join current thread`). Een afgelopen wachter wist alleen zijn eigen `alarm_time`.
- **Gedeelde callback overschreven**: `scheduler.alarm_manager.set_alarm(tijd, callback)` (spraak-Q&A in de bedtijd-routine) zet `alarm.callback = morning_routine` op het GEDEELDE object; een daarna vanaf het dashboard gezette wekker draaide stilzwijgend die routine i.p.v. de gekozen (`alarm.routine`, bv. een eigen routine). `POST /api/alarm` zet de callback nu expliciet terug.
- **Afgegane wekker werd bij elke herstart opnieuw ingepland**: de UI zegt na afgaan "Geen wekker gezet", maar `alarm.time` bleef in `settings.json` en de opstart-code her-armde 'm bij elke herstart (crash/reboot/update) -> de volgende ochtend ging de radio ongevraagd aan. Nieuwe sleutel `alarm.armed` (False na afgaan/wissen, True bij zetten); oude settings zonder sleutel tellen als armed (geen verlies van een openstaande wekker bij de eerste herstart na deploy). `alarm.time` blijft als voorinvulling van het tijdveld.
- **Dubbele run**: `/api/routines/run` draait synchroon en zonder guard; dubbelklik/twee tabbladen/wekker-tijdens-handmatig gaf twee tegelijk lopende routines (dubbele TTS, dubbele radio, twee vastgezette threads). Nu per routine-id één tegelijk: tweede aanroep geeft 409 `routine 'x' is al bezig`; lock wordt ook na een exceptie vrijgegeven; andere routines blijven parallel mogelijk. Home-knoppen zijn nu ook disabled tijdens de run (routines.js deed dat al).
- **Bestanden**: `scheduler/alarm.py`, `Dashboard/backend/routines_api.py`, `Dashboard/static/scripts/home.js`, `tests/test_alarm.py`.
- **Resterend risico**: het AI-tool-pad (`start_morning_routine` in `logic/gpt_handler.py`) roept de routine direct aan en valt buiten de guard (een dubbele "start de ochtendroutine" in de chat kan nog twee runs geven; bewust niet gekoppeld om `logic/` niet van de Flask-laag te laten afhangen). Een via spraak gezette wekker wordt niet in `settings.json` bewaard (overleeft geen herstart). Wekker blijft één gedeeld slot.

#### S2-10 Spraakpijplijn: hangende opname, cloud-upload van kamergeluid, log-spam, PortAudio-herstel
Tests: `tests/test_voice.py` (14 tests). Niet uitvoerbaar zonder hardware: echte USB-mic-replug/PortAudio-gedrag (nagebootst met een nep-`sounddevice`).
- **(kosten/privacy) Cloud-fallback op de wake-lus**: `stt.transcribe` valt bij een falend lokaal model door naar de OpenAI-API; de wake-lus transcribeert elke ~2s een 2s-stukje kamergeluid. Met een `OPENAI_API_KEY` (die de chat ook gebruikt) en een kapot lokaal model (model niet gecached + geen internet, OOM) ging dus 24/7 kamergeluid naar een betaalde cloud-API (~$0,006/min = ~$8,6/dag). Nu `cloud_fallback=False` voor alle wake-chunks (`whisperrr`, `wacht_op_wakeword`); alleen `stt.backend: openai` (bewuste keuze) of een expliciete opdracht (`cloud_fallback=True`, default) gebruikt de cloud nog.
- **Request zonder key**: `_tr_openai` deed zonder key toch een POST met `Bearer ` (bewezen tegen de oude code: request geregistreerd met header `Bearer `) -> 401 per ronde. Nu `RuntimeError("OPENAI_API_KEY ontbreekt")` zonder netwerk.
- **Modellen**: `_get_fw()`/`_tr_whisper` zonder lock (twee threads = twee modellen in RAM) en een niet-ladend model werd elke ~2s opnieuw geprobeerd. Nu lock + 60s-backoff per backend (fout blijft zichtbaar in `[stt]`-regels).
- **Hangende opname**: `sd.wait()` heeft geen timeout; een verdwenen USB-mic kon de luisterlus (en de routine-thread van de bedtijd-routine in het dashboard) voor altijd laten hangen. Nu wachten tot `duur + 5s`, dan `sd.stop()` + `NoMicError`.
- **Mic-herstel**: PortAudio leest de apparaatlijst één keer; een losgetrokken/opnieuw aangesloten mic bleef tot een procesherstart onvindbaar, en `_record_rate_cache` bleef van het oude apparaat. Na `NoMicError` nu cache wissen + `sd._terminate()`/`_initialize()` (aparte stappen, zodat `_initialize` ook draait als `_terminate` faalt; privé-API, alleen als er geen stream actief is).
- **Log-spam**: elke mislukte ronde schreef een regel (mic weg: 30s -> 2880/dag; generieke fout 2s -> 43k/dag). Nu dezelfde melding 1x per 10 min (+ "ronde N op rij"), generieke fouten met verdubbelende pauze (2->30s), en een ronde zonder fout (ook "geen wake-woord gehoord") reset de teller. Porcupine-fallback-melding idem.
- **Bestanden**: `ai/stt.py`, `voice/Whisper.py`, `voice/Whisper_short.py`, `tests/test_voice.py`.
- **Resterend risico**: `sd._terminate()` is privé-API van sounddevice; getest tegen een nep-module, niet tegen een echte hot-plug. De voice-lus draait niet in de systemd-service (alleen `main.py assistant/all` en de bedtijd-routine).

#### S2-11 (P2) Falende detector telde als "lege kamer"; Meldingen-endpoint las alle logs
- **Probleem**: `logic.people_detect.count_people()` gaf bij een detectiefout (kapotte cv2-build, geheugentekort, ...) `0` terug. De presence-worker kan een `0` niet onderscheiden van een echte lege meting en telde die mee voor de EMPTY-kant: na `empty_grace_s` ging de lamp UIT terwijl er iemand zat, en bleef uit (AAN vereist juist een geslaagde detectie). Bewezen: end-to-end test met kapotte HOG -> oude code `EMPTY`, nieuwe code `OCCUPIED` + status `unknown`.
- **Fix**: `count_people(..., error_value=0)` (default = oud contract, ook voor ongeldige frames); presence roept 'm met `error_value=None` aan en behandelt `None` als sensor ONBEKEND (zelfde hold-logica als "geen frame": toestand vast, geen lampactie, grace start pas na herstel, 1 logregel i.p.v. per tick). "Sensor available again" wordt nu pas gelogd na een geslaagde detectie (anders flapte het per tick). Detectiefout-print gededupliceerd (1x/10 min i.p.v. elke 3s naar journald). HOG-singleton achter een lock (eerste aanroep uit twee threads).
- **`/api/notifications`**: las per poll (30s, alleen als de pagina open staat) de volledige laatste 4 logbestanden (`rglob` + `read_text`); nu alleen het staartje (achterwaarts lezen in blokken, gemeten < 200 kB uit een 3,4 MB-bestand), `?limit=abc` gaf een 500 (ongevangen `ValueError`) -> default 40, begrensd 1..500, en gebruikt dezelfde logmap als `log()` (`logic.logger.BASE_LOG_DIR`).
- **Bestanden**: `logic/people_detect.py`, `Dashboard/backend/presence.py`, `Dashboard/backend/system_api.py`, `tests/test_presence_detection.py` (6), `tests/test_notifications.py` (9).
- **Resterend risico**: HOG-detectie zelf blijft de zwakke schakel (zittend/liggend); een permanent falende detector laat de kamer eeuwig 'onbekend' (lamp blijft in de laatste stand) -- zichtbaar in `/api/presence` (`state: unknown`) en in het log.

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
