"""SMTP email delivery (Mailpit locally, any SMTP server in production)."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid

from app.core.config import Settings, get_settings


class EmailNotConfiguredError(RuntimeError):
    pass


class TemporaryEmailError(RuntimeError):
    """Worth retrying later (connection problems, 4xx responses)."""


class PermanentEmailError(RuntimeError):
    """Retrying will not help (rejected recipient, 5xx responses)."""


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    html: str
    text: str


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, email: OutgoingEmail) -> None:
        s = self.settings
        if not s.smtp_configured:
            raise EmailNotConfiguredError("SMTP is not configured (set SMTP_HOST)")
        message = EmailMessage()
        message["From"] = s.email_from
        message["To"] = email.to
        message["Subject"] = email.subject
        message["Message-ID"] = make_msgid(domain="smartshelf.local")
        message.set_content(email.text)
        message.add_alternative(email.html, subtype="html")
        try:
            if s.smtp_use_ssl:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=ssl.create_default_context()
                )
            else:
                client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
            with client:
                if s.smtp_use_tls and not s.smtp_use_ssl:
                    client.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    client.login(s.smtp_username, s.smtp_password)
                client.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            raise PermanentEmailError("Recipient address was rejected by the mail server") from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentEmailError("SMTP authentication failed; check SMTP_USERNAME/SMTP_PASSWORD") from exc
        except smtplib.SMTPResponseException as exc:
            # Never include server text verbatim: it can echo credentials.
            if 400 <= exc.smtp_code < 500:
                raise TemporaryEmailError(
                    f"Mail server temporarily refused the message (code {exc.smtp_code})"
                ) from exc
            raise PermanentEmailError(f"Mail server rejected the message (code {exc.smtp_code})") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise TemporaryEmailError(f"Could not reach the mail server ({exc.__class__.__name__})") from exc


def get_email_sender() -> SmtpEmailSender:
    return SmtpEmailSender(get_settings())
