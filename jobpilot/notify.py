from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def send_email(settings: Settings, subject: str, html_body: str) -> None:
    if not (settings.smtp_user and settings.smtp_password and settings.notify_email):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.smtp_user, settings.notify_email
    msg.set_content("Open the dashboard to review: " + settings.public_url)
    msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        log.warning("email failed: %s", e)


def push(settings: Settings, title: str, message: str, url: str = "") -> None:
    """Phone push via ntfy.sh: install the ntfy app and subscribe to your NTFY_TOPIC."""
    if not settings.ntfy_topic:
        return
    try:
        httpx.post(f"https://ntfy.sh/{settings.ntfy_topic}", content=message.encode(),
                   headers={"Title": title, **({"Click": url} if url else {})}, timeout=15)
    except Exception as e:  # noqa: BLE001
        log.warning("push failed: %s", e)


def digest(settings: Settings, jobs: list[dict[str, Any]]) -> None:
    if not jobs:
        return
    rows = "".join(
        f"<tr><td style='padding:6px 10px;font-weight:600'>{j.get('score', '')}</td>"
        f"<td style='padding:6px 10px'><a href='{settings.public_url}/job/{html.escape(j['uid'])}'>"
        f"{html.escape(j['title'])}</a><br><span style='color:#666'>{html.escape(j['company'])} · "
        f"{html.escape(j.get('location') or '')}</span><br><span style='font-size:13px'>"
        f"{html.escape((j.get('assessment') or {}).get('summary', ''))}</span></td></tr>"
        for j in jobs)
    body = (f"<p>{len(jobs)} new job(s) ready for your review.</p><table>{rows}</table>"
            f"<p><a href='{settings.public_url}'>Open dashboard</a></p>")
    send_email(settings, f"JobPilot: {len(jobs)} new match(es) to review", body)
    top = jobs[0]
    push(settings, f"{len(jobs)} new job match(es)", f"Top: {top['title']} at {top['company']} ({top.get('score')})",
         settings.public_url)
