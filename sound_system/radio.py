import threading
import time

import vlc

from logic.logger import log

_shared = None
_shared_lock = threading.Lock()


def shared_player() -> "RadioPlayer":
    """DE RadioPlayer van dit proces. Het dashboard (services.svc("radio")), de AI-tools (gpt_handler) en de
    wekker-/ochtendroutine gebruiken allemaal deze ene instantie. Voorheen maakte elk hun EIGEN
    ``RadioPlayer()`` (de ochtendroutine zelfs bij elke run een nieuwe, die nergens werd bewaard): een
    radio die via de wekker of de chat startte kon dan niet met de Stop-knop van het dashboard worden
    gestopt ("Radio speelde niet"), twee streams konden tegelijk spelen, en elke ochtend bleef er een
    VLC-instantie (threads, geheugen) achter."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = RadioPlayer()
        return _shared


class RadioPlayer:
    # Eén bewerking tegelijk (klasse-breed: er hoort maar één speler te zijn). play() duurt tot ~4,5 s; twee
    # gelijktijdige aanroepen (wekker + dashboard-klik) mengden anders stop()/set_media()/play().
    _lock = threading.RLock()
    last_error = None

    def __init__(self):
        # --quiet + --no-plugins-cache onderdrukt de VLC-ruis op stderr.
        self.instance = vlc.Instance("--quiet", "--no-plugins-cache", "--intf", "dummy")
        if self.instance is None:  # ongeldige args op sommige VLC-builds
            self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()
        self.start_time = None
        self.current_station_name = None
        self.stations = {
            "radio538": "http://22333.live.streamtheworld.com/RADIO538.mp3",
            "qmusic": "https://playerservices.streamtheworld.com/api/livestream-redirect/QMUSIC.mp3",
            "nporadio2": "http://icecast.omroep.nl/radio2-bb-mp3",
            "skyradio": "http://19993.live.streamtheworld.com/SKYRADIO.mp3",
            "npo3fm": "http://icecast.omroep.nl/3fm-bb-mp3",
            "slam": "https://stream.radiocorp.nl/web11_mp3",
            "radio10": "https://stream.radio10.nl/radio10",
            "radio1": "http://icecast.omroep.nl/radio1-bb-mp3",
            "100nl": "https://stream.100p.nl/100pctnl.mp3",
            "veronica": "https://25323.live.streamtheworld.com/VERONICA.mp3",
        }

    def play(self, station_name):
        with self._lock:
            return self._play_locked(station_name)

    def _play_locked(self, station_name):
        # last_error: None = gelukt. Laat de API (media_api.radio_play) een mislukte
        # start als fout melden i.p.v. als "success" (de tekst-uitkomst blijft
        # ongewijzigd voor de spraak/AI-aanroepers).
        self.last_error = None
        if not station_name:
            self.last_error = "Geen station opgegeven"
            return self.last_error

        station_key = station_name.lower().replace(" ", "")
        url = self.stations.get(station_key)

        if not url:
            log("Radio", f"Station '{station_name}' niet gevonden!")
            self.last_error = f"Station '{station_name}' niet gevonden"
            return self.last_error

        # Pas NU de huidige zender stoppen: een onbekende/lege zendernaam stopte
        # voorheen eerst de lopende radio en meldde daarna 'niet gevonden'.
        self.player.stop()
        if self.player.is_playing():
            self.player.stop()
            time.sleep(0.3)

        self.player.audio_set_volume(70)
        media = self.instance.media_new(url)
        self.player.set_media(media)
        self.player.play()
        log("Radio", f"Speelt nu: {station_name}")

        # Wacht tot de stream écht speelt i.p.v. altijd blind 2s te slapen --
        # de meeste streams starten ruim binnen 2s, waardoor dit request
        # voorheen onnodig lang bleef hangen (waitress-workerthread bezet).
        # Zelfde max. wachttijd (2x2s) en dezelfde herstart-poging als voorheen,
        # nu alleen adaptief i.p.v. altijd de volle tijd te wachten.
        if not self._wait_playing(2.0):
            self.player.play()
            self._wait_playing(2.0)

        state = self.player.get_state()
        if state != vlc.State.Playing:
            self.last_error = "Radio kon niet starten (check URL of internet)."
            return self.last_error
        self.start_time = time.time()
        self.current_station_name = station_name
        return f"Speelt nu: {station_name}"

    def _wait_playing(self, timeout: float, interval: float = 0.1) -> bool:
        """Poll tot de player Playing meldt, of tot timeout. Return of het lukte."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.player.get_state() == vlc.State.Playing:
                return True
            time.sleep(interval)
        return self.player.get_state() == vlc.State.Playing

    def stop(self):
        with self._lock:
            if self.player.is_playing():
                self.player.stop()
                log("Radio", "Radio gestopt")
                return "Radio gestopt"
            return "Radio speelde niet"

    def pause(self):
        with self._lock:
            if self.player.is_playing():
                self.player.pause()
                log("Radio", "Radio gepauzeerd")
                return "Radio gepauzeerd"
            return "Radio speelde niet"

    def resume(self):
        with self._lock:
            if not self.player.is_playing():
                self.player.play()
                log("Radio", "Radio verder afgespeeld")
                return "Radio verder afgespeeld"
            return "Radio was al bezig"
    
    def set_volume(self, volume: int):
        """Stel volume van de radio in (0-100)"""
        try:
            volume = max(0, min(100, int(volume)))
            self.player.audio_set_volume(volume)
            log("Radio", f"Volume ingesteld op {volume}%")
            return True
        except Exception as e:
            log("Radio", f"Fout bij volume: {e}")
            return False

    def current_station(self):
        state = self.player.get_state()
        if state == vlc.State.Playing:
            return {"type": "radio", "station": getattr(self, "current_station_name", "Onbekend")}
        else:
            return {"type": "none", "station": None}
        


