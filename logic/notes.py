import json
import os
from datetime import datetime
from pathlib import Path

NOTES_FILE = Path(__file__).resolve().parent.parent / "data" / "notes.json"


def load_notes() -> dict:
    if not NOTES_FILE.exists():
        return {}
    try:
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_notes(notes: dict) -> None:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NOTES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(notes, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, NOTES_FILE)


def add_note(note: str, category: str = "default") -> None:
    notes = load_notes()
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
    notes = load_notes()
    entries = notes.get(category, [])
    if 0 <= index < len(entries):
        entries.pop(index)
        save_notes(notes)
        return True
    return False
