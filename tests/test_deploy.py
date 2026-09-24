"""Deploy-scripts en bestandsrechten (statische tests; de Pi zelf is hier niet nodig)."""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [ROOT / "deploy" / "setup-pi.sh", ROOT / "deploy" / "update-pi.sh"]


def _bash():
    exe = shutil.which("bash")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return None
    return exe if r.stdout.strip() == "ok" else None


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_deploy_scripts_pass_bash_syntax_check(script):
    bash = _bash()
    if not bash:
        pytest.skip("bash niet beschikbaar")
    r = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_deploy_scripts_pass_shellcheck(script):
    exe = shutil.which("shellcheck")
    if not exe:
        pytest.skip("shellcheck niet geinstalleerd (pip install shellcheck-py)")
    r = subprocess.run([exe, "-x", str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_setup_unit_exists_helper_is_independent_of_systemctl_exit_status():
    """'systemctl list-unit-files <unit>' geeft niet overal een betrouwbare
    exit-status; zonder deze helper brak 'systemctl restart ollama' (bij een
    niet-geinstalleerde Ollama) de hele setup af door 'set -e'."""
    bash = _bash()
    if not bash:
        pytest.skip("bash niet beschikbaar")
    text = (ROOT / "deploy" / "setup-pi.sh").read_text(encoding="utf-8")
    m = re.search(r"^unit_exists\(\) \{.*\}$", text, re.M)
    assert m, "unit_exists()-helper ontbreekt in setup-pi.sh"
    helper = m.group(0)

    def run(stub_output):
        script = (f'systemctl() {{ printf "%s" "{stub_output}"; return 0; }}\n'
                  f"{helper}\nunit_exists ollama.service && echo JA || echo NEE")
        r = subprocess.run([bash, "-c", script], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()

    assert run("") == "NEE"                                   # exit 0 maar geen match -> bestaat niet
    assert run("ollama.service enabled enabled") == "JA"


def test_setup_guards_ollama_dropin_and_update_has_health_check():
    setup = (ROOT / "deploy" / "setup-pi.sh").read_text(encoding="utf-8")
    assert "command -v ollama >/dev/null 2>&1 && unit_exists ollama.service" in setup
    update = (ROOT / "deploy" / "update-pi.sh").read_text(encoding="utf-8")
    assert "/api/config" in update and "git reset --hard $PREV" in update


# --------------------------------------------------------------------------- #
# .env en tokenbestand: alleen de eigenaar (0600)
# --------------------------------------------------------------------------- #
def test_set_secret_restricts_env_file_to_owner(monkeypatch, tmp_path):
    import config
    import config.settings as cs

    calls = []
    real_chmod = os.chmod
    monkeypatch.setattr(cs.os, "chmod", lambda p, m: (calls.append((str(p), m)), real_chmod(p, m))[1])
    config.set_secret("TAPO_USER", "iemand@example.com")

    assert any(Path(p).name == ".env" and m == 0o600 for p, m in calls), calls
    if os.name == "posix":
        assert stat.S_IMODE(cs.ENV_FILE.stat().st_mode) == 0o600


def test_google_token_refresh_keeps_the_token_file_owner_only(monkeypatch, tmp_path):
    """De atomische tmp+replace-schrijfactie gaf het token (refresh-token!)
    anders de default umask-rechten terug, ook als het eerst 0600 was."""
    import scheduler.agenda as agenda_mod

    token_path = tmp_path / "google_calendar_token.json"
    token_path.write_text('{"token": "oud"}', encoding="utf-8")
    monkeypatch.setattr(agenda_mod, "GOOGLE_TOKEN_FILE", token_path)

    class FakeCreds:
        expired = True
        refresh_token = "rt"

        def refresh(self, request):
            pass

        def to_json(self):
            return '{"token": "nieuw"}'

    import google.oauth2.credentials as gcred
    monkeypatch.setattr(gcred.Credentials, "from_authorized_user_file", lambda *a, **kw: FakeCreds())
    calls = []
    monkeypatch.setattr(agenda_mod.os, "chmod", lambda p, m: calls.append((Path(p).name, m)))

    agenda_mod.GoogleCalendarAccount("a@b.c", "cid", "cs")._credentials()
    assert ("google_calendar_token.json.tmp", 0o600) in calls, calls
