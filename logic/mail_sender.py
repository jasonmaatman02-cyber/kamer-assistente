"""E-mail helpers (log mailer). Uses the SMTP settings from ``.env``."""
from __future__ import annotations

import os
import smtplib
import tempfile
import zipfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config
from logic.logger import log

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "data" / "logs"
MAX_ATTACH_BYTES = 20 * 1024 * 1024  # Gmail weigert >25 MB; hou marge


def _smtp():
    server = config.secret("SMTP_SERVER", "smtp.gmail.com")
    port = int(config.secret("SMTP_PORT", "587") or 587)
    if port == 465:
        return smtplib.SMTP_SSL(server, port, timeout=20)
    s = smtplib.SMTP(server, port, timeout=20)
    s.starttls()
    return s


def _send(msg) -> bool:
    addr, pw = config.secret("EMAIL_ADDRESS"), config.secret("EMAIL_PASSWORD")
    if not (addr and pw):
        log("ERROR", "E-mail niet geconfigureerd (.env)")
        return False
    try:
        with _smtp() as server:
            server.login(addr, pw)
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"Fout bij verzenden e-mail: {exc}")
        log("ERROR", f"Fout bij verzenden e-mail: {exc}")
        return False


def send_email_message(subject: str, message: str, to: str | None = None) -> str:
    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = config.secret("EMAIL_ADDRESS")
    msg["To"] = to or config.secret("RECEIVER")
    if _send(msg):
        log("E-mail", f"E-mail verzonden: {subject}")
        return "E-mail verzonden"
    return "E-mail verzenden mislukt"


def zip_logs_and_send(max_months: int = 3) -> str:
    """Zip de logs van de laatste ``max_months`` maanden en mail ze."""
    if not LOGS_DIR.is_dir():
        return "Geen logs om te versturen"

    month_dirs = sorted((d for d in LOGS_DIR.iterdir() if d.is_dir()), reverse=True)[:max_months]
    fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="logs_")
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for md in month_dirs:
                for f in md.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(LOGS_DIR))

        size = os.path.getsize(zip_path)
        if size == 0:
            return "Geen logs om te versturen"
        if size > MAX_ATTACH_BYTES:
            log("ERROR", f"Logs-zip te groot ({size} bytes)")
            return f"Logs te groot om te mailen ({size // (1024*1024)} MB)"

        msg = MIMEMultipart()
        msg["From"] = config.secret("EMAIL_ADDRESS")
        msg["To"] = config.secret("RECEIVER")
        msg["Subject"] = "Logs"
        msg.attach(MIMEText("Hierbij de logs.", "plain"))
        with open(zip_path, "rb") as f:
            part = MIMEApplication(f.read(), Name="logs.zip")
        part["Content-Disposition"] = 'attachment; filename="logs.zip"'
        msg.attach(part)

        if _send(msg):
            log("E-mail", "Logs verzonden")
            return "Logs verzonden"
        return "Logs verzenden mislukt"
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
