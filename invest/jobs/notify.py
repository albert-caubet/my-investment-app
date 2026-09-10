"""Short failure notifications: Telegram message and/or email, when configured.

The report itself travels through ``invest.report.deliver``; this is the
one-line alert a scheduler cannot send on its own ("weekly job: 3 problems").
Nothing raises: a notification that cannot be sent is returned as a status line.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from invest.report.deliver import email_config, send_email, telegram_config


def send_telegram_text(text: str, cfg: dict) -> str:
    data = urllib.parse.urlencode({"chat_id": cfg["chat_id"], "text": text[:4000]}).encode("utf-8")
    request = urllib.request.Request(f"https://api.telegram.org/bot{cfg['token']}/sendMessage", data=data)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"telegram: {payload}")
    return "telegram alert sent"


def notify(subject: str, text: str) -> list[str]:
    statuses = []
    telegram = telegram_config()
    if telegram:
        try:
            statuses.append(send_telegram_text(f"{subject}\n{text}", telegram))
        except Exception as exc:
            statuses.append(f"telegram alert FAILED: {exc}")
    email = email_config()
    if email:
        try:
            statuses.append(send_email(subject, f"<pre>{text}</pre>", email, text_alternative=text))
        except Exception as exc:
            statuses.append(f"email alert FAILED: {exc}")
    if not statuses:
        statuses.append("no alert channel configured")
    return statuses
