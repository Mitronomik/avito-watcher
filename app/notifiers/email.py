import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailNotifier:
    channel_name = "email"

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        sender: str | None = None,
        recipient: str | None = None,
    ) -> None:
        self.enabled = settings.email_enabled if enabled is None else enabled
        self.host = settings.smtp_host if host is None else host
        self.port = settings.smtp_port if port is None else port
        self.username = settings.smtp_username if username is None else username
        self.password = settings.smtp_password if password is None else password
        self.sender = settings.email_from if sender is None else sender
        self.recipient = settings.email_to if recipient is None else recipient

    def _is_configured(self) -> bool:
        return all([self.host, self.port, self.sender, self.recipient])

    async def send_listing_alert(self, message: str, payload: dict) -> bool:
        if not self.enabled:
            logger.info("Email notifier is disabled; skipping listing alert")
            return False

        if not self._is_configured():
            logger.warning("Email notifier is misconfigured; skipping listing alert")
            return False

        if self.username and not self.password:
            logger.warning("Email notifier is misconfigured: SMTP password is required when username is set")
            return False

        subject = self._build_subject(payload)
        await asyncio.to_thread(self._send_sync, subject, message)
        return True

    def _send_sync(self, subject: str, message: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg.set_content(message)

        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port) as smtp:
                self._login_if_needed(smtp)
                smtp.send_message(msg)
            return

        with smtplib.SMTP(self.host, self.port) as smtp:
            if self.port == 587:
                smtp.starttls()
            self._login_if_needed(smtp)
            smtp.send_message(msg)

    def _login_if_needed(self, smtp: Any) -> None:
        if self.username:
            smtp.login(self.username, self.password)

    def _build_subject(self, payload: dict) -> str:
        search_name = payload.get("search_name") or "search"
        price = payload.get("price") or "n/a"
        area = payload.get("area_m2") or "n/a"
        title = (payload.get("title") or "listing")[:80]
        return f"[{search_name}] {price} ₽ • {area} м² • {title}"
