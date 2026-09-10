"""Archive the report and, when configured, send it by email or Telegram.

The archive is always written. Email needs ``SMTP_HOST``, ``SMTP_PORT``,
``SMTP_USER``, ``SMTP_PASSWORD``, ``REPORT_EMAIL_TO`` (and optionally
``REPORT_EMAIL_FROM``); Telegram needs ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID``. Each delivery returns a status line for the run record.
"""

from __future__ import annotations

import json
import mimetypes
import smtplib
import ssl
import urllib.request
import uuid
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from invest.paths import reports_dir
from invest.secrets import get_secret


def archive(markdown_text: str, html_text: str, run_at: datetime, *, directory: Path | None = None) -> dict[str, Path]:
    """``report_YYYY-MM-DD_HHMM.md`` and ``.html``, plus ``latest.md`` / ``latest.html`` copies."""
    target = directory or reports_dir()
    target.mkdir(parents=True, exist_ok=True)
    stem = f"report_{run_at:%Y-%m-%d_%H%M}"
    md_path, html_path = target / f"{stem}.md", target / f"{stem}.html"
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    (target / "latest.md").write_text(markdown_text, encoding="utf-8")
    (target / "latest.html").write_text(html_text, encoding="utf-8")
    return {"markdown": md_path, "html": html_path}


def email_config() -> dict | None:
    host, to = get_secret("SMTP_HOST"), get_secret("REPORT_EMAIL_TO")
    if not host or not to:
        return None
    return {
        "host": host,
        "port": int(get_secret("SMTP_PORT", "587") or 587),
        "user": get_secret("SMTP_USER"),
        "password": get_secret("SMTP_PASSWORD"),
        "to": to,
        "from": get_secret("REPORT_EMAIL_FROM") or get_secret("SMTP_USER") or to,
    }


def send_email(subject: str, html_text: str, cfg: dict, *, text_alternative: str = "") -> str:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["from"]
    message["To"] = cfg["to"]
    message.set_content(text_alternative or "The weekly report is attached as HTML.")
    message.add_alternative(html_text, subtype="html")
    context = ssl.create_default_context()
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=60) as server:
        server.ehlo()
        server.starttls(context=context)
        if cfg.get("user") and cfg.get("password"):
            server.login(cfg["user"], cfg["password"])
        server.send_message(message)
    return f"email sent to {cfg['to']} via {cfg['host']}"


def telegram_config() -> dict | None:
    token, chat = get_secret("TELEGRAM_BOT_TOKEN"), get_secret("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return None
    return {"token": token, "chat_id": chat}


def send_telegram(html_path: Path, caption: str, cfg: dict) -> str:
    """``sendDocument`` with the HTML file attached (multipart, no extra dependency)."""
    boundary = uuid.uuid4().hex
    data = html_path.read_bytes()
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{cfg['chat_id']}\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1000]}\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{html_path.name}\"\r\n"
        f"Content-Type: {mimetypes.guess_type(html_path.name)[0] or 'text/html'}\r\n\r\n",
    ]
    body = "".join(parts).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg['token']}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"telegram: {payload}")
    return f"telegram document sent to chat {cfg['chat_id']}"


def deliver(markdown_text: str, html_text: str, run_at: datetime, *, summary: str, directory: Path | None = None,
            send: bool = True) -> tuple[dict[str, Path], list[str]]:
    """Archive, then each configured channel; a failed channel is a status line, not an exception."""
    paths = archive(markdown_text, html_text, run_at, directory=directory)
    statuses = [f"archived {paths['html']}"]
    if not send:
        statuses.append("delivery skipped")
        return paths, statuses
    email_cfg = email_config()
    if email_cfg:
        try:
            statuses.append(send_email(f"Weekly report {run_at:%Y-%m-%d}", html_text, email_cfg, text_alternative=summary))
        except Exception as exc:
            statuses.append(f"email FAILED: {exc}")
    telegram_cfg = telegram_config()
    if telegram_cfg:
        try:
            statuses.append(send_telegram(paths["html"], summary, telegram_cfg))
        except Exception as exc:
            statuses.append(f"telegram FAILED: {exc}")
    if not email_cfg and not telegram_cfg:
        statuses.append("no delivery channel configured (SMTP_HOST/REPORT_EMAIL_TO or TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
    return paths, statuses
