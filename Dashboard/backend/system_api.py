"""System / settings / overview endpoints."""
import json
import shutil
import subprocess
import sys
import threading
import time

from flask import Blueprint, jsonify, request

import config
from Dashboard.backend import services as S
from Dashboard.backend.auth import mail_ready, password_set, require_password

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({"settings": config.all_settings(), "defaults": config.DEFAULTS, "errors": S.errors()})


@system_bp.route("/api/settings", methods=["POST"])
@require_password
def set_settings():
    patch = request.get_json(silent=True) or {}
    if not isinstance(patch, dict):
        return jsonify({"success": False, "error": "verwacht een JSON-object"}), 400
    config.update(patch)
    S.reset_services()
    return jsonify({"success": True, "settings": config.all_settings()})


@system_bp.route("/api/config")
def public_config():
    return jsonify({
        "poll": config.get("dashboard"),
        "camera": {
            "browser_detection": config.get("camera.browser_detection"),
            "enabled": config.get("camera.enabled"),
            "detect_threshold": config.get("camera.detect_threshold", 0.5),
            "lamp_quiet_from": config.get("camera.lamp_quiet_from", "21:40"),
            "lamp_quiet_to": config.get("camera.lamp_quiet_to", "07:00"),
        },
        "features": config.get("features"),
    })


@system_bp.route("/api/system_stats")
def system_stats():
    data = S.system_stats()
    return jsonify(data), (500 if "error" in data else 200)


@system_bp.route("/api/service_status")
def service_status():
    return jsonify(S.service_status())


@system_bp.route("/api/weather")
def weather():
    # de losse route (Environment-pagina "verversen") wil verse data;
    # /api/overview mag de cache van services gebruiken
    data = S.weather_data(request.args.get("city"), fresh=True)
    return jsonify(data), (503 if data.get("error") else 200)


@system_bp.route("/api/calendar_today")
def calendar_today():
    return jsonify(S.calendar_today(fresh=True))


@system_bp.route("/api/overview")
def overview():
    from logic.notes import list_all_notes

    return jsonify({
        "system": S.system_stats(),
        "services": S.service_status(),
        "weather": S.weather_data(),
        "calendar": S.calendar_today(),
        "now_playing": S.current_playing(),
        "notes": list_all_notes(),
    })


# --------------------------------------------------------------------------- #
# Wifi-snelheidstest (on-demand; verbruikt echte data, dus nooit automatisch)
# --------------------------------------------------------------------------- #
_speed = {"status": "idle", "result": None, "error": None, "started": 0.0}  # idle|running|done|error
_SPEED_STALE_AFTER = 240   # een 'running' die langer hangt is vastgelopen -> opnieuw mogen
_speed_lock = threading.Lock()


def _speedtest_ookla():
    """Officiële Ookla CLI als die op PATH staat (betrouwbaarder dan de lib).
    Geeft None terug als 'ie er niet is of het niet de Ookla-versie is."""
    exe = shutil.which("speedtest")
    if not exe:
        return None
    try:
        ver = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        if "Ookla" not in (ver.stdout + ver.stderr):
            return None  # dit is de python 'speedtest-cli', niet de Ookla CLI
        out = subprocess.run(
            [exe, "--format=json", "--progress=no", "--accept-license", "--accept-gdpr"],
            capture_output=True, text=True, timeout=180,
        )
        data = json.loads(out.stdout or "{}")
        if "download" not in data:
            return None
        return {
            "down_mbps": round(data["download"]["bandwidth"] * 8 / 1e6, 1),
            "up_mbps": round(data["upload"]["bandwidth"] * 8 / 1e6, 1),
            "ping_ms": round(data.get("ping", {}).get("latency", 0), 1),
            "server": (data.get("server") or {}).get("name"),
            "engine": "ookla",
        }
    except Exception:  # noqa: BLE001 - val terug op de python-lib
        return None


def _speedtest_python():
    import speedtest  # pip: speedtest-cli

    s = speedtest.Speedtest(secure=True)
    s.get_best_server()
    down = s.download() / 1e6
    up = s.upload(pre_allocate=False) / 1e6
    r = s.results.dict()
    return {
        "down_mbps": round(down, 1),
        "up_mbps": round(up, 1),
        "ping_ms": round(r.get("ping", 0), 1),
        "server": (r.get("server") or {}).get("sponsor"),
        "engine": "speedtest-cli",
    }


def _run_speedtest():
    t0 = time.time()
    try:
        res = _speedtest_ookla() or _speedtest_python()
        res["took_s"] = round(time.time() - t0, 1)
        res["tested_at"] = time.strftime("%Y-%m-%d %H:%M")
        with _speed_lock:
            _speed.update(status="done", result=res, error=None)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        if "No module named" in msg:
            msg = "speedtest-cli niet geïnstalleerd — draai deploy/update-pi.sh"
        with _speed_lock:
            _speed.update(status="error", error=msg)


@system_bp.route("/api/speedtest", methods=["GET"])
def speedtest_status():
    with _speed_lock:
        return jsonify(dict(_speed))


@system_bp.route("/api/speedtest", methods=["POST"])
def speedtest_start():
    # De kaart staat op de open Overview; een 'running'-guard voorkomt spam.
    # Geen wachtwoord dus, net als de andere LAN-bediening.
    now = time.time()
    with _speed_lock:
        if _speed["status"] == "running" and now - _speed["started"] < _SPEED_STALE_AFTER:
            return jsonify({"status": "running"})
        _speed.update(status="running", error=None, started=now)
    threading.Thread(target=_run_speedtest, daemon=True, name="speedtest").start()
    return jsonify({"status": "running"})


# --------------------------------------------------------------------------- #
# Health — één plek om over SSH/curl te zien of alles nog leeft
# --------------------------------------------------------------------------- #
def _git_sha() -> str:
    """De commit die NU op schijf staat (kan nieuwer zijn dan wat er draait)."""
    try:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent.parent
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


_BOOT_SHA = _git_sha()   # vastgelegd bij het starten van dit proces


def _ollama_reachable() -> bool | None:
    if (config.get("ai.backend") or "ollama").lower() != "ollama":
        return None
    try:
        import requests

        url = (config.get("ai.ollama_url") or "http://localhost:11434").rstrip("/")
        return requests.get(f"{url}/api/tags", timeout=2).ok
    except Exception:  # noqa: BLE001
        return False


@system_bp.route("/api/health")
def health():
    from Dashboard.backend import routines_api
    from Dashboard.backend.camera_api import camera

    dt = routines_api._alarm.alarm_time
    on_disk = _git_sha()
    return jsonify({
        "ok": True,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git": on_disk,                          # commit op schijf
        "git_running": _BOOT_SHA,                # commit waarmee dit proces startte
        "restart_pending": on_disk != _BOOT_SHA and "?" not in (on_disk, _BOOT_SHA),
        "python": sys.version.split()[0],
        "server": "waitress" if "waitress" in sys.modules else "flask-dev",
        "system": S.system_stats(),
        "services": S.service_status(),
        "camera": {
            "error": camera.error,
            "viewers": camera._viewers,
            "thread_alive": bool(camera._thread and camera._thread.is_alive()),
        },
        "alarm": {"set": dt is not None, "when": dt.strftime("%Y-%m-%d %H:%M") if dt else None},
        "ollama_reachable": _ollama_reachable(),
        "mail_ready": mail_ready(),
        "password_set": password_set(),
        "thread_count": threading.active_count(),
        # alleen de 'eigen' threads; waitress/idle-ruis weggelaten
        "threads": sorted(
            t.name for t in threading.enumerate()
            if t.is_alive() and not t.name.startswith(("waitress-", "Thread-"))
        ),
    })


@system_bp.route("/api/notifications")
def notifications():
    from datetime import datetime
    from pathlib import Path

    limit = int(request.args.get("limit", 40))
    base = Path(__file__).resolve().parent.parent.parent / "data" / "logs"
    lines: list[str] = []
    if base.is_dir():
        for fp in sorted(base.rglob("*.txt"))[-4:]:
            try:
                lines.extend(fp.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass
    parsed = []
    for ln in lines[-limit:][::-1]:
        ln = ln.strip()
        if not ln:
            continue
        subject, message, tijd = "", ln, ""
        if ln.startswith("[") and "]" in ln:
            try:
                tijd = ln[1:ln.index("]")]
                rest = ln[ln.index("]") + 1:].strip()
                if rest.startswith("[") and "]" in rest:
                    subject = rest[1:rest.index("]")]
                    message = rest[rest.index("]") + 1:].strip()
                else:
                    message = rest
            except ValueError:
                pass
        parsed.append({"time": tijd, "subject": subject, "message": message})
    return jsonify({"notifications": parsed, "date": datetime.now().strftime("%Y-%m-%d")})
