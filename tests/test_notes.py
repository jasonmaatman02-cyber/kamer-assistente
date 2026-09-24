"""logic/notes.py: add_note()/delete_note() doen een load-wijzig-save-cyclus
op een JSON-bestand. Zonder lock kan een gelijktijdige aanroep de andere
overschrijven (klassieke lost-update race) -- deze tests bewaken dat dat
niet meer gebeurt."""
import threading


def test_add_and_get_note():
    from logic.notes import add_note, get_notes

    add_note("boodschappen doen", category="taken")
    assert get_notes("taken")[0]["note"] == "boodschappen doen"


def test_delete_note():
    from logic.notes import add_note, delete_note, get_notes

    add_note("eerste", category="x")
    add_note("tweede", category="x")
    assert delete_note(0, category="x") is True
    assert [n["note"] for n in get_notes("x")] == ["tweede"]


def test_delete_note_out_of_range_returns_false():
    from logic.notes import delete_note

    assert delete_note(99, category="leeg") is False


def test_concurrent_add_note_does_not_lose_updates():
    """Regressie: zonder lock om load->wijzig->save konden twee gelijktijdige
    add_note()-aanroepen elkaar overschrijven (allebei lezen dezelfde oude
    staat, wie het laatst schrijft wint -- de ander is spoorloos weg)."""
    from logic.notes import add_note, get_notes

    barrier = threading.Barrier(10)

    def adder(i):
        barrier.wait(timeout=2)
        add_note(f"note-{i}", category="race")

    threads = [threading.Thread(target=adder, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    saved = {n["note"] for n in get_notes("race")}
    assert saved == {f"note-{i}" for i in range(10)}   # geen enkele verdwenen


def test_concurrent_add_and_delete_stay_consistent():
    from logic.notes import add_note, delete_note, get_notes

    for i in range(5):
        add_note(f"seed-{i}", category="mix")

    barrier = threading.Barrier(2)

    def adder():
        barrier.wait(timeout=2)
        add_note("nieuw", category="mix")

    def deleter():
        barrier.wait(timeout=2)
        delete_note(0, category="mix")

    t1 = threading.Thread(target=adder)
    t2 = threading.Thread(target=deleter)
    t1.start(); t2.start()
    t1.join(timeout=5); t2.join(timeout=5)

    # 5 seed + 1 toegevoegd - 1 verwijderd = 5, en het bestand blijft geldige JSON
    assert len(get_notes("mix")) == 5


# --- grenzen en kapot bestand -------------------------------------------- #
import pytest  # noqa: E402


def test_note_length_and_count_are_bounded(client):
    from logic import notes as N

    assert client.post("/api/notes", json={"note": "x" * (N.MAX_NOTE_CHARS + 1)}).status_code == 400
    assert client.post("/api/notes", json={"note": "   "}).status_code == 400
    assert client.post("/api/notes", json={"note": "x" * N.MAX_NOTE_CHARS}).status_code == 200

    N.save_notes({"default": [{"note": "n", "timestamp": "t"}] * N.MAX_NOTES})
    r = client.post("/api/notes", json={"note": "een te veel"})
    assert r.status_code == 400 and "Te veel notities" in r.get_json()["error"]
    assert client.delete("/api/notes/0").get_json()["success"] is True
    assert client.post("/api/notes", json={"note": "nu past het weer"}).status_code == 200


def test_add_note_rejects_empty_and_coerces_non_strings():
    from logic.notes import add_note, get_notes

    with pytest.raises(ValueError):
        add_note("  ")
    add_note(123, category="typen")
    assert get_notes("typen")[0]["note"] == "123"


@pytest.mark.parametrize("content", ["{kapot", "[]", '"x"', "null"])
def test_corrupt_notes_file_is_kept_not_overwritten(content):
    from logic import notes as N

    N.NOTES_FILE.write_text(content, encoding="utf-8")
    assert N.load_notes() == {}
    kept = list(N.NOTES_FILE.parent.glob(f"{N.NOTES_FILE.name}.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == content

    N.add_note("nieuw begin")                      # schrijft een vers bestand
    assert [n["note"] for n in N.get_notes("default")] == ["nieuw begin"]
    assert kept[0].exists()
