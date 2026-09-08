import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BASE_LOG_DIR = BASE_DIR / "data" / "logs"


def log(subject, message):
    now = datetime.now()
    folder = BASE_LOG_DIR / now.strftime("%Y-%m")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        line = f"[{now.strftime('%H:%M:%S')}] [{subject}] {message}\n"
        with open(folder / f"{now.strftime('%Y-%m-%d')}.txt", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        # Logging mag de app nooit laten crashen.
        print(f"[logger] kon niet schrijven: {exc}")
