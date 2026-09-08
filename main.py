"""Entry point.

  python main.py            -> interactieve keuze
  python main.py dashboard  -> alleen het webdashboard
  python main.py assistant  -> alleen de spraakassistent
  python main.py all        -> allebei (dashboard in een thread, spraak op de voorgrond)
"""
import sys
import threading

import config
from logic.logger import log


def run_dashboard(block: bool = True):
    from rundashboard import main as _run

    _run()


def run_assistant():
    from voice.tts_output import speak
    from voice.Whisper import whisperrr
    from scheduler.alarm_manager import check_alarm, cancel_alarm

    log("Programma", "Spraakassistent gestart")
    speak("Programma gestart.")
    try:
        while True:
            if check_alarm():
                print("Alarm gaat NU af!")
                cancel_alarm()
            whisperrr()
    except KeyboardInterrupt:
        print("\nGestopt.")


def run_all():
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
    run_assistant()


def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else None
    if mode is None:
        if sys.stdin.isatty():
            try:
                mode = {"1": "dashboard", "2": "all", "3": "assistant"}.get(
                    input("1: dashboard, 2: dashboard + assistent, 3: alleen assistent? ").strip(),
                    "dashboard",
                )
            except (EOFError, KeyboardInterrupt):
                mode = "dashboard"
        else:
            # geen terminal (bv. via de desktop-app of een service) -> gewoon het dashboard
            mode = "dashboard"

    if mode == "dashboard":
        run_dashboard()
    elif mode == "assistant":
        run_assistant()
    elif mode == "all":
        run_all()
    else:
        print(f"onbekende modus: {mode}")


if __name__ == "__main__":
    main()
