"""deploy/update-pi.sh draait de update echt (in tijdelijke git-repo's, met systemctl/sudo/curl/pip als stubs) en
draait zichzelf terug als de nieuwe versie niet gezond wordt of pip mislukt. Alleen op Linux/macOS (bash + stubs)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash-stubs; draait op Linux (CI en Pi)")

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "update-pi.sh"


def _run(cmd, cwd, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise AssertionError(f"{cmd} faalde:\n{r.stdout}\n{r.stderr}")
    return r


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return _run(["git", *args], cwd, env)


@pytest.fixture()
def rig(tmp_path):
    """origin + een 'dev'-clone (die de nieuwe versie pusht) + de 'Pi'-clone waarin het script draait."""
    origin, dev, pi, bin_dir = tmp_path / "origin.git", tmp_path / "dev", tmp_path / "pi", tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    calls.write_text("")

    def stub(name, body):
        p = bin_dir / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(0o755)

    stub("systemctl", f'echo "systemctl $*" >> {calls}\ncase "$1" in show) echo 3min;; esac\nexit 0')
    stub("sudo", 'exec "$@"')
    stub("journalctl", "echo 'journal (stub)'")
    stub("sleep", "exit 0")
    # 'gezond' zolang er geen BROKEN-bestand in de repo staat (curl draait in de repo-map van het script)
    stub("curl", f'echo "curl" >> {calls}\n[ -e BROKEN ] && exit 22\nexit 0')

    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(dev))
    _git(dev, "symbolic-ref", "HEAD", "refs/heads/main")
    (dev / "deploy").mkdir()
    shutil.copy(SCRIPT, dev / "deploy" / "update-pi.sh")
    (dev / ".gitignore").write_text(".venv/" + chr(10))
    (dev / "requirements-dashboard.txt").write_text("# geen echte pakketten\n")
    (dev / "notes.txt").write_text("origineel\n")
    (dev / "VERSION").write_text("v1\n")
    _git(dev, "add", "-A")
    _git(dev, "commit", "-q", "-m", "v1")
    _git(dev, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(pi))
    (pi / ".venv" / "bin").mkdir(parents=True)
    pip = pi / ".venv" / "bin" / "pip"
    pip.write_text('#!/bin/bash\n[ -e "$(dirname "$0")/../../PIPFAIL" ] && exit 1\nexit 0\n')
    pip.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull)

    def publish(**files):
        for name, content in files.items():
            (dev / name).write_text(content)
        _git(dev, "add", "-A")
        _git(dev, "commit", "-q", "-m", "v2")
        _git(dev, "push", "-q", "origin", "main")

    def deploy():
        return _run(["bash", str(pi / "deploy" / "update-pi.sh")], pi, env, check=False)

    def head():
        return _git(pi, "rev-parse", "--short", "HEAD").stdout.strip()

    return type("Rig", (), {"pi": pi, "dev": dev, "calls": calls, "publish": staticmethod(publish),
                            "deploy": staticmethod(deploy), "head": staticmethod(head)})


def test_a_healthy_update_is_kept(rig):
    before = rig.head()
    rig.publish(VERSION="v2\n")
    r = rig.deploy()
    assert r.returncode == 0, r.stdout + r.stderr
    assert rig.head() != before and (rig.pi / "VERSION").read_text() == "v2\n"
    assert "dashboard OK" in r.stdout
    assert rig.calls.read_text().count("systemctl restart") == 1


def test_an_unhealthy_update_is_rolled_back_and_the_old_version_restarted(rig):
    before = rig.head()
    rig.publish(BROKEN="x\n", VERSION="v2\n")            # de stub-curl meldt 'ongezond' zodra BROKEN bestaat
    r = rig.deploy()
    assert r.returncode == 1
    assert rig.head() == before                         # code staat weer op de vorige commit
    assert (rig.pi / "VERSION").read_text() == "v1\n" and not (rig.pi / "BROKEN").exists()
    assert "teruggedraaid" in r.stdout and "NIET actief" in r.stdout
    assert rig.calls.read_text().count("systemctl restart") == 2      # kapotte versie + de teruggedraaide


def test_a_failing_pip_install_rolls_back_without_restarting(rig):
    before = rig.head()
    (rig.pi / "PIPFAIL").write_text("")                                  # ongetrackt: de pip-stub faalt zolang dit bestaat
    rig.publish(VERSION="v2\n")
    r = rig.deploy()
    assert r.returncode == 1
    assert "NIET doorgevoerd" in r.stdout
    assert rig.head() == before and (rig.pi / "VERSION").read_text() == "v1\n"
    assert "systemctl restart" not in rig.calls.read_text()             # de draaiende service is niet aangeraakt


def test_rollback_keeps_local_changes(rig):
    """Lokale wijzigingen (bv. data/notes.json op de Pi) mogen bij een terugdraaiactie niet verloren gaan."""
    before = rig.head()
    (rig.pi / "notes.txt").write_text("lokaal aangepast\n")
    rig.publish(BROKEN="x\n", VERSION="v2\n")
    r = rig.deploy()
    assert r.returncode == 1 and rig.head() == before
    assert (rig.pi / "notes.txt").read_text() == "lokaal aangepast\n"
