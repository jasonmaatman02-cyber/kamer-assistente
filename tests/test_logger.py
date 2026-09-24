"""Het logboek: een regel per melding, begrensd, en nooit een exceptie."""
import logic.logger as L


def _lines(tmp_path):
    files = list((tmp_path / "logs").rglob("*.txt"))
    assert len(files) == 1
    return files[0].read_text(encoding="utf-8").splitlines()


def test_a_multiline_message_cannot_forge_extra_log_lines(tmp_path):
    L.log("AI", "kort\n[12:00:00] [ERROR] valse melding\r\nnog een")
    lines = _lines(tmp_path)
    assert len(lines) == 1
    assert lines[0].endswith("kort | [12:00:00] [ERROR] valse melding | nog een")


def test_a_huge_message_is_truncated(tmp_path):
    L.log("AI", "x" * 100_000)
    (line,) = _lines(tmp_path)
    assert len(line) < L._MAX_LINE_CHARS + 100 and line.endswith("(ingekort)")


def test_subject_is_sanitised_too(tmp_path):
    L.log("EVIL]\n[FAKE", "boodschap")
    (line,) = _lines(tmp_path)
    assert "\n" not in line and "boodschap" in line


def test_non_string_values_and_empty_messages_are_fine(tmp_path):
    L.log("X", 123)
    L.log("X", None)
    L.log("X", "")
    assert len(_lines(tmp_path)) == 3


def test_an_unwritable_log_directory_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "geen-map"
    blocker.write_text("ik ben een bestand", encoding="utf-8")
    monkeypatch.setattr(L, "BASE_LOG_DIR", blocker / "logs")       # mkdir faalt: bovenliggend pad is een bestand
    L.log("X", "mag niet crashen")
