import datetime
import re
import threading
import time

from logic.logger import log

# Een Raspberry Pi 4 heeft geen batterijklok: na een stroomstoring start hij op met de tijd van de laatste
# sync en springt de klok een paar seconden na de start (NTP) uren of dagen vooruit -- na het instellen
# van de wekker uit de config. Zonder correctie ging de wekker dan direct af ("07:00" van gisteren is
# opeens verleden tijd, om 10:00 's ochtends) of juist een dag te laat. Een sprong van de wandklok t.o.v.
# de monotone klok groter dan dit telt als klokstap.
_CLOCK_STEP_S = 120.0
# Na een klokstap gaat een wekker die hooguit zo lang geleden had moeten afgaan alsnog af (het echte
# uur was net gepasseerd); een oudere wordt naar de eerstvolgende keer verschoven.
_LATE_TOLERANCE_S = 600.0


def parse_alarm_time(text) -> tuple[int, int] | None:
    """(uur, minuut) uit een tekst, of None. De tijd komt ook uit een LLM ("zet dit om naar uur:minuut"),
    dat soms extra tekst meegeeft: de eerste, losse ``\\d{1,2}\\d{2}`` in "2026-09-24 07:30" gaf 20:26. Daarom
    eerst de duidelijkste vormen (met ':' of '.', dan 'uur'/'u'/'h'), pas daarna het losse 4-cijferpatroon."""
    t = str(text)
    strict = [
        r"(?<![\d:.])(\d{1,2})[:.](\d{2})(?![\d])",                 # 07:30, 7.30
        r"(?<!\d)(\d{1,2})\s*(?:uur|u|h)\s*(\d{2})(?!\d)",          # 7 uur 30, 7u30, 7h30
        r"(?<!\d)(\d{1,2})\s*(?:uur|u|h)(?![a-z0-9])",                # 7 uur  -> 07:00
    ]
    for pat in strict + [r"(?<!\d)(\d{1,2})(\d{2})(?!\d)"]:                  # laatste: 0730, 730
        for m in re.finditer(pat, t, re.IGNORECASE):
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else 0
            if 0 <= hour < 24 and 0 <= minute < 60:              # "24.09.2026 om 08:15": de datum overslaan
                return hour, minute
    return None


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
        clock0 = (time.time(), time.monotonic())
        tod = None                      # (uur, minuut) van een wekker op een tijdstip; None = vaste datetime
        if when is not None:
            alarm_dt = when
        else:
            parsed = parse_alarm_time(time_str)
            if parsed is None:
                log("Alarm", f"Kon tijd niet lezen: {time_str!r}")
                print(f"Kon tijd niet lezen: {time_str!r}")
                return None
            hour, minute = parsed
            if not (0 <= hour < 24 and 0 <= minute < 60):
                log("Alarm", f"Ongeldige tijd: {hour}:{minute}")
                return None
            tod = (hour, minute)
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
                target=self._wait_for_alarm, args=(stop, alarm_dt, tod, clock0), daemon=True
            )
            self.alarm_thread.start()
        return alarm_dt

    @staticmethod
    def _after_clock_step(tod: tuple[int, int], now: datetime.datetime) -> datetime.datetime:
        """Wekkertijdstip na een klokstap: vandaag als dat hooguit _LATE_TOLERANCE_S geleden was of nog
        komt, anders morgen."""
        today = now.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
        if (now - today).total_seconds() > _LATE_TOLERANCE_S:
            today += datetime.timedelta(days=1)
        return today

    def _wait_for_alarm(self, stop: threading.Event, alarm_dt: datetime.datetime,
                        tod: tuple[int, int] | None = None, clock0: tuple[float, float] | None = None):
        # Efficiënt wachten: slaap tot de wekkertijd (in blokken van max 30s zodat
        # een systeemklok-sprong of lange slaapstand wordt opgevangen), en word
        # meteen wakker bij cancel/vervanging.
        wall0, mono0 = clock0 or (time.time(), time.monotonic())
        while not stop.is_set():
            wall, mono = time.time(), time.monotonic()
            step = (wall - wall0) - (mono - mono0)
            wall0, mono0 = wall, mono
            if tod is not None and abs(step) > _CLOCK_STEP_S:
                new_dt = self._after_clock_step(tod, datetime.datetime.now())
                log("Alarm", f"Systeemklok sprong {step:+.0f} s; wekker {alarm_dt:%Y-%m-%d %H:%M} -> {new_dt:%Y-%m-%d %H:%M}")
                with self._lock:
                    if stop.is_set():
                        return
                    if self.alarm_time == alarm_dt:
                        self.alarm_time = new_dt
                alarm_dt = new_dt
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
