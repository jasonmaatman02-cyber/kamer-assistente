import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

NOTES_FILE = Path(__file__).resolve().parent.parent / "data" / "notes.json"
# Beschermt de load-wijzig-save-volgorde in add_note()/delete_note(): zonder
# lock kan bij (bijna-)gelijktijdige aanroepen (twee tabbladen, of dashboard +
# spraakassistent tegelijk) de ene wijziging de andere stilzwijgend
# overschrijven (allebei lezen dezelfde oude staat, wie het laatst schrijft
# wint -- de ander is spoorloos weg).
_notes_lock = threading.Lock()


MAX_NOTE_CHARS = 2000
MAX_NOTES = 1000     # totaal; /api/overview stuurt ze allemaal mee en elke wijziging herschrijft het bestand


def _quarantine_corrupt_notes() -> None:
    """Een onleesbaar notes.json niet stilzwijgend laten overschrijven door de eerstvolgende
    add_note() (die begint dan met een lege set): bewaar het als notes.json.corrupt-<tijd>."""
    try:
        target = NOTES_FILE.with_name(f"{NOTES_FILE.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        os.replace(NOTES_FILE, target)
        print(f"[notes] kapot notitiebestand bewaard als {target.name}")
    except OSError as exc:
        print(f"[notes] kon kapot notitiebestand niet opzij zetten: {exc}")


def load_notes() -> dict:
    if not NOTES_FILE.exists():
        return {}
    try:
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("verwacht een JSON-object")
        return data
    except OSError:
        return {}
    except ValueError:                       # JSONDecodeError is een ValueError
        _quarantine_corrupt_notes()
        return {}


def save_notes(notes: dict) -> None:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NOTES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(notes, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, NOTES_FILE)


def add_note(note: str, category: str = "default") -> None:
    """Voegt een notitie toe. Gooit ValueError bij een lege/te lange notitie of te veel notities."""
    note = str(note).strip()
    if not note:
        raise ValueError("Lege notitie")
    if len(note) > MAX_NOTE_CHARS:
        raise ValueError(f"Notitie te lang (max {MAX_NOTE_CHARS} tekens)")
    with _notes_lock:
        notes = load_notes()
        if sum(len(v) for v in notes.values() if isinstance(v, list)) >= MAX_NOTES:
            raise ValueError(f"Te veel notities (max {MAX_NOTES}) -- verwijder er eerst een paar")
        notes.setdefault(category, []).append(
            {"note": note, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        )
        save_notes(notes)


def get_notes(category: str | None = None):
    notes = load_notes()
    if category:
        return notes.get(category, [])
    return notes


def list_all_notes() -> list[str]:
    """Flat list of ``"timestamp: note"`` strings across every category."""
    out: list[str] = []
    for entries in load_notes().values():
        for e in entries:
            out.append(f"{e.get('timestamp', '')}: {e.get('note', '')}".strip(": ").strip())
    return out


def delete_note(index: int, category: str = "default") -> bool:
    with _notes_lock:
        notes = load_notes()
        entries = notes.get(category, [])
        if 0 <= index < len(entries):
            entries.pop(index)
            save_notes(notes)
            return True
        return False
