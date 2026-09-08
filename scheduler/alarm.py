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
        self.alarm_time = alarm_dt

        log("Alarm", f"Wekker ingesteld op {alarm_dt.strftime('%Y-%m-%d %H:%M')}")
        print(f"Wekker ingesteld op {alarm_dt.strftime('%H:%M')}")

        if self.alarm_thread and self.alarm_thread.is_alive():
            self._stop_event.set()
            self.alarm_thread.join()

        self._stop_event.clear()
        self.alarm_thread = threading.Thread(target=self._wait_for_alarm, daemon=True)
        self.alarm_thread.start()
        return alarm_dt

    def _wait_for_alarm(self):
        # Efficiënt wachten: slaap tot de wekkertijd (in blokken van max 30s zodat
        # een systeemklok-sprong of lange slaapstand wordt opgevangen), en word
        # meteen wakker bij cancel.
        while not self._stop_event.is_set():
            remaining = (self.alarm_time - datetime.datetime.now()).total_seconds()
            if remaining <= 0:
                break
            if self._stop_event.wait(min(remaining, 30)):
                return  # geannuleerd
        if self._stop_event.is_set():
            return
        print("Wekker gaat af!")
        log("Alarm", "Wekker gaat af")
        cb = self.callback
        self.alarm_time = None
        if callable(cb):
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                log("Alarm", f"Fout in wekker-callback: {exc}")

    def cancel_alarm(self):
        self._stop_event.set()
        if self.alarm_thread and self.alarm_thread.is_alive():
            self.alarm_thread.join(timeout=2)
        self.alarm_time = None
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
