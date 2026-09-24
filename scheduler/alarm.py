import datetime
import re
import threading

from logic.logger import log


class AlarmScheduler:
    def __init__(self, callback=None):
        self.alarm_time = None
        self.callback = callback
        self.alarm_thread = None
        self._stop_event = threading.Event()
        # Beschermt de check-then-act op alarm_thread/alarm_time hieronder --
        # zonder lock konden twee (bijna-)gelijktijdige set_alarm()-aanroepen
        # (bv. een dubbele form-submit) allebei de oude thread als "niet meer
        # levend genoeg om te stoppen" zien en zo allebei een eigen wekker-
        # thread starten. Zelfde patroon als PresenceWorker.start() elders.
        self._lock = threading.Lock()

    def set_alarm(self, time_str=None, when: datetime.datetime | None = None):
        """Zet een wekker. Geef 'HH:MM' (ook '7 uur 30' / '7.30' werkt) via
        ``time_str``, of een volledige ``datetime`` via ``when``. Retourneert de
        geplande datetime, of None als de tijd niet te lezen is."""
        if when is not None:
            alarm_dt = when
        else:
            m = re.search(r"(\d{1,2})\s*(?:[:.hu]|uur)?\s*(\d{2})", str(time_str))
            if not m:
                log("Alarm", f"Kon tijd niet lezen: {time_str!r}")
                print(f"Kon tijd niet lezen: {time_str!r}")
                return None
            hour, minute = int(m.group(1)), int(m.group(2))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                log("Alarm", f"Ongeldige tijd: {hour}:{minute}")
                return None
            now = datetime.datetime.now()
            alarm_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if alarm_dt <= now:
                alarm_dt += datetime.timedelta(days=1)

        log("Alarm", f"Wekker ingesteld op {alarm_dt.strftime('%Y-%m-%d %H:%M')}")
        print(f"Wekker ingesteld op {alarm_dt.strftime('%H:%M')}")

        with self._lock:
            # Elke wachter-thread krijgt zijn EIGEN stop-event en zijn eigen
            # tijdstip. De vorige wachter stoppen we alleen (event zetten) en
            # joinen we bewust NIET: is die al afgegaan, dan draait zijn
            # callback (= een hele routine: LLM-groet, TTS, radio -- minuten)
            # in die thread, en een join() zonder timeout onder deze lock
            # liet elke POST /api/alarm en elke cancel zo lang hangen.
            self._stop_event.set()
            stop = self._stop_event = threading.Event()
            self.alarm_time = alarm_dt
            self.alarm_thread = threading.Thread(
                target=self._wait_for_alarm, args=(stop, alarm_dt), daemon=True
            )
            self.alarm_thread.start()
        return alarm_dt

    def _wait_for_alarm(self, stop: threading.Event, alarm_dt: datetime.datetime):
        # Efficiënt wachten: slaap tot de wekkertijd (in blokken van max 30s zodat
        # een systeemklok-sprong of lange slaapstand wordt opgevangen), en word
        # meteen wakker bij cancel/vervanging.
        while not stop.is_set():
            remaining = (alarm_dt - datetime.datetime.now()).total_seconds()
            if remaining <= 0:
                break
            if stop.wait(min(remaining, 30)):
                return  # geannuleerd of vervangen
        with self._lock:
            if stop.is_set():
                return  # cancel/set_alarm kwam er net tussen
            # Alleen 'onze' wekker wissen; een nieuwe wekker blijft staan.
            if self.alarm_time == alarm_dt:
                self.alarm_time = None
            cb = self.callback
        print("Wekker gaat af!")
        log("Alarm", "Wekker gaat af")
        if callable(cb):
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                log("Alarm", f"Fout in wekker-callback: {exc}")

    def cancel_alarm(self):
        with self._lock:
            self._stop_event.set()
            thread = self.alarm_thread
            self.alarm_time = None
        # Buiten de lock joinen: de wachter pakt die lock zelf ook (zie
        # _wait_for_alarm), en een routine-callback mag nooit op zichzelf wachten.
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)   # een wachtende thread stopt direct; een afgegane draait zijn routine door
        log("Alarm", "Wekker geannuleerd")
        print("Wekker geannuleerd.")

    def check_alarm(self):
        """True als de wekker NU had moeten afgaan en de thread nog loopt."""
        if self.alarm_time is None:
            return False
        return (
            datetime.datetime.now() >= self.alarm_time
            and self.alarm_thread is not None
            and self.alarm_thread.is_alive()
        )
