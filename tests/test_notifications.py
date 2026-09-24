"""/api/notifications: alleen het staartje van de logbestanden lezen, robuuste limit."""
import builtins

import pytest

from logic import logger


def _write(day_dir, name, lines):
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / name).write_text("".join(f"{l}\n" for l in lines), encoding="utf-8")


@pytest.fixture()
def logs(tmp_path):
    return logger.BASE_LOG_DIR   # door conftest al naar tmp_path/logs omgeleid


def test_returns_newest_first_across_files(client, logs):
    _write(logs / "2026-08", "2026-08-30.txt", [f"[10:00:0{i}] [A] oud {i}" for i in range(3)])
    _write(logs / "2026-09", "2026-09-01.txt", [f"[11:00:0{i}] [B] nieuw {i}" for i in range(3)])
    d = client.get("/api/notifications?limit=4").get_json()["notifications"]
    assert [n["message"] for n in d] == ["nieuw 2", "nieuw 1", "nieuw 0", "oud 2"]
    assert d[0] == {"time": "11:00:02", "subject": "B", "message": "nieuw 2"}


def test_matches_the_previous_full_read_implementation(client, logs):
    _write(logs / "2026-09", "2026-09-01.txt", [f"[10:00:{i:02d}] [S] a{i}" for i in range(50)])
    _write(logs / "2026-09", "2026-09-02.txt", ["", "zonder tijd", "[12:00:00] geen onderwerp"] + [f"[12:01:{i:02d}] [T] b{i}" for i in range(20)])
    # verwachte volgorde (nieuwste eerst), leeg regeltje telt mee in de limit maar wordt overgeslagen
    file1 = [f"a{i}" for i in range(50)]
    file2 = [None, "zonder tijd", "geen onderwerp"] + [f"b{i}" for i in range(20)]
    everything = file1 + file2
    for limit in (1, 5, 23, 24, 40, 100):
        got = client.get(f"/api/notifications?limit={limit}").get_json()["notifications"]
        want = [m for m in everything[-limit:][::-1] if m is not None]
        assert [n["message"] for n in got] == want, limit
    got = client.get("/api/notifications?limit=100").get_json()["notifications"]
    assert got[-1]["message"] == "a0" and got[0]["message"] == "b19"


def test_does_not_read_whole_big_logfiles(client, logs, monkeypatch):
    """Een logbestand van een dag kan na een fout-storm MB's zijn; de Meldingen-pagina
    pollt elke 30s. Er mag dus maar een staartje gelezen worden."""
    big = [f"[10:{i // 60 % 60:02d}:{i % 60:02d}] [X] regel {i}" for i in range(120_000)]   # ~3,4 MB
    _write(logs / "2026-09", "2026-09-05.txt", big)
    size = (logs / "2026-09" / "2026-09-05.txt").stat().st_size
    assert size > 3_000_000

    read = {"bytes": 0}
    real_open = builtins.open

    class Counting:
        def __init__(self, f):
            self._f = f

        def read(self, *a):
            data = self._f.read(*a)
            read["bytes"] += len(data)
            return data

        def __getattr__(self, name):
            return getattr(self._f, name)

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *a):
            return self._f.__exit__(*a)

    from pathlib import Path

    from Dashboard.backend import system_api

    def whole_file(self, *a, **k):
        raise AssertionError("het hele logbestand werd gelezen")

    monkeypatch.setattr(Path, "read_text", whole_file)
    monkeypatch.setattr(system_api, "open", lambda p, *a, **k: Counting(real_open(p, *a, **k)), raising=False)
    d = client.get("/api/notifications?limit=100").get_json()["notifications"]

    assert len(d) == 100
    assert d[0]["message"] == "regel 119999"
    assert read["bytes"] < 200_000, read


@pytest.mark.parametrize("q,expected_max", [("abc", 40), ("-5", 1), ("0", 1), ("99999", 500), ("", 40)])
def test_limit_is_validated_not_a_500(client, logs, q, expected_max):
    _write(logs / "2026-09", "2026-09-05.txt", [f"[10:00:00] [X] r{i}" for i in range(600)])
    r = client.get(f"/api/notifications?limit={q}")
    assert r.status_code == 200
    assert len(r.get_json()["notifications"]) == expected_max


def test_no_logs_dir_is_fine(client):
    assert client.get("/api/notifications").get_json()["notifications"] == []
