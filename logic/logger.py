from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BASE_LOG_DIR = BASE_DIR / "data" / "logs"


_MAX_LINE_CHARS = 2000


def _one_line(value, limit: int) -> str:
    """Een logregel is precies een regel: regeleinden in (door gebruikers/het model bepaalde) tekst
    zouden anders neppe regels als "[12:00:00] [ERROR] ..." in het logboek/de Meldingen-pagina kunnen
    zetten, en een enorme uitkomst (tool-resultaat, foutpagina) blaast het dagbestand op."""
    text = " | ".join(str(value).splitlines())          # splitlines() kent alle regeleinde-varianten
    return text if len(text) <= limit else text[:limit] + "... (ingekort)"


def log(subject, message):
    now = datetime.now()
    subject = _one_line(subject, 60)
    message = _one_line(message, _MAX_LINE_CHARS)
    folder = BASE_LOG_DIR / now.strftime("%Y-%m")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        line = f"[{now.strftime('%H:%M:%S')}] [{subject}] {message}\n"
        with open(folder / f"{now.strftime('%Y-%m-%d')}.txt", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        # Logging mag de app nooit laten crashen.
        print(f"[logger] kon niet schrijven: {exc}")
