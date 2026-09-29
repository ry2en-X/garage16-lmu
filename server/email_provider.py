"""email_provider.py — Abstracted email sending (V0.6.9, spec §6).

Two implementations, selected by LMU_GARAGE_EMAIL_PROVIDER (config.py):

  - DevelopmentEmailProvider (default): logs the email instead of sending
    it. This is what a NAS/localhost deployment uses — there is no real
    mail infrastructure there, and there doesn't need to be one for
    the operator and a handful of friends to test the password-reset flow.
  - SmtpEmailProvider: real SMTP delivery, for a deployment with actual
    SMTP credentials (a VPS pointed at a real provider). NOT exercised
    against a live SMTP server in this project's development sandbox (no
    network egress to arbitrary SMTP ports there) — reviewed for
    correctness, same caveat this project already gives Docker/PostgreSQL
    in docker-compose.yml's own header comment. Test this for real before
    relying on it in production, same as everything else under
    docs/ROLLBACK.md's spirit.

Neither implementation is called from anywhere yet except
routers/accounts.py's password-reset endpoints — this module has no
other side effects on import.
"""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from .config import settings

logger = logging.getLogger("lmu_garage.email")


class EmailProvider(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> None:
        ...


class DevelopmentEmailProvider(EmailProvider):
    def send(self, to: str, subject: str, body: str) -> None:
        logger.info(
            "[dev-email] Would send to %s | subject: %s\n%s", to, subject, body
        )


class SmtpEmailProvider(EmailProvider):
    def send(self, to: str, subject: str, body: str) -> None:
        if not settings.smtp_host:
            raise RuntimeError(
                "LMU_GARAGE_EMAIL_PROVIDER=smtp but LMU_GARAGE_SMTP_HOST is not set."
            )
        message = EmailMessage()
        message["From"] = settings.smtp_from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)


def get_email_provider() -> EmailProvider:
    if settings.email_provider == "smtp":
        return SmtpEmailProvider()
    return DevelopmentEmailProvider()
