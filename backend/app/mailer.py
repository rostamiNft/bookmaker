"""Outgoing e-mail: password-reset links.

Plain ``smtplib`` - no service, no SDK, no telemetry. When SMTP is not configured the
message is *not* silently dropped: :func:`send_mail` reports ``sent=False`` with the
reason, and the API either hands the link to an administrator (who can pass it on by
hand) or logs it. Tests use :mod:`aiosmtpd` (BSD-3) to run a real local SMTP server.
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from .config import settings


@dataclass
class SendResult:
    sent: bool
    method: str = ""        # smtp-starttls | smtp-ssl | smtp-plain | disabled
    error: str = ""
    to: str = ""
    subject: str = ""

    def as_dict(self) -> dict:
        return {"sent": self.sent, "method": self.method, "error": self.error, "to": self.to, "subject": self.subject}


def send_mail(to: str, subject: str, body: str, html: str = "") -> SendResult:
    """Send one message. Never raises: the caller decides what a failure means."""
    if not to or "@" not in to:
        return SendResult(False, "disabled", "no recipient address", to, subject)
    if not settings.smtp_configured:
        return SendResult(False, "disabled", "SMTP is not configured (BOOKMAKER_SMTP_HOST / _FROM)", to, subject)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        if settings.smtp_starttls and settings.smtp_port != 465:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
            return SendResult(True, "smtp-starttls", "", to, subject)
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20,
                                  context=ssl.create_default_context()) as s:
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
            return SendResult(True, "smtp-ssl", "", to, subject)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return SendResult(True, "smtp-plain", "", to, subject)
    except Exception as e:  # noqa: BLE001 - a mail failure must never break a request
        return SendResult(False, "smtp", f"{type(e).__name__}: {e}"[:300], to, subject)


def reset_mail(link: str, minutes: int, username: str) -> tuple[str, str, str]:
    """(subject, plain body, html body) for a password-reset link."""
    subject = "Bookmaker: reset your password"
    body = (f"Hello {username},\n\n"
            f"Someone asked to reset the Bookmaker password for this account.\n"
            f"Open the link below within {minutes} minutes to choose a new password:\n\n    {link}\n\n"
            "If you did not ask for this, ignore this message: the link does nothing on its own.\n")
    html = (f"<p>Hello {html_escape(username)},</p>"
            "<p>Someone asked to reset the Bookmaker password for this account.</p>"
            f"<p><a href=\"{html_escape(link)}\">Choose a new password</a> (valid for {minutes} minutes).</p>"
            "<p>If you did not ask for this, ignore this message: the link does nothing on its own.</p>")
    return subject, body, html


def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
