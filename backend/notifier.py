import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional


@dataclass
class NotifierConfig:
    smtp_host: Optional[str] = os.getenv("SMTP_HOST")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: Optional[str] = os.getenv("SMTP_USER")
    smtp_password: Optional[str] = os.getenv("SMTP_PASSWORD")
    from_email: Optional[str] = os.getenv("SMTP_FROM")


class Notifier:
    """Send email notifications when summaries are ready.

    The notifier is a no-op unless an SMTP host is provided. This keeps
    development workflows simple while enabling production-ready alerts
    with standard email infrastructure.
    """

    def __init__(self, config: Optional[NotifierConfig] = None):
        self.config = config or NotifierConfig()
        self.logger = logging.getLogger(__name__)

    def notify(self, to_email: Optional[str], subject: str, body: str) -> None:
        if not to_email:
            return
        if not self.config.smtp_host or not self.config.from_email:
            self.logger.info("Skipping email notification; SMTP not configured")
            return

        message = EmailMessage()
        message["From"] = self.config.from_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        try:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=20) as smtp:
                smtp.starttls()
                if self.config.smtp_user and self.config.smtp_password:
                    smtp.login(self.config.smtp_user, self.config.smtp_password)
                smtp.send_message(message)
            self.logger.info("Notification email sent to %s", to_email)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to send notification to %s: %s", to_email, exc)
