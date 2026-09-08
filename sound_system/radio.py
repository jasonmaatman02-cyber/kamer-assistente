import vlc
import time
from logic.logger import log


class RadioPlayer:
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
        self.player.stop()
        if not station_name:
            return "Geen station opgegeven"

        station_key = station_name.lower().replace(" ", "")
        url = self.stations.get(station_key)

        if not url:
            log("Radio", f"Station '{station_name}' niet gevonden!")
            return f"Station '{station_name}' niet gevonden"

        if self.player.is_playing():
            self.player.stop()
            time.sleep(0.3)

        self.player.audio_set_volume(70)
        media = self.instance.media_new(url)
        self.player.set_media(media)
        self.player.play()
        log("Radio", f"Speelt nu: {station_name}")

        time.sleep(2)
        self.player.play()
        time.sleep(2)

        state = self.player.get_state()
        if state != vlc.State.Playing:
            return "Radio kon niet starten (check URL of internet)."
        self.start_time = time.time()
        self.current_station_name = station_name
        return f"Speelt nu: {station_name}"

    def stop(self):
        if self.player.is_playing():
            self.player.stop()
            log("Radio", "Radio gestopt")
            return "Radio gestopt"
        return "Radio speelde niet"

    def pause(self):
        if self.player.is_playing():
            self.player.pause()
            log("Radio", "Radio gepauzeerd")
            return "Radio gepauzeerd"
        return "Radio speelde niet"

    def resume(self):
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
        


