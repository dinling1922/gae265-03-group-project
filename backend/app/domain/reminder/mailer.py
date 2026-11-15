"""寄出提醒信（ADR-0031）：只依 SMTP 設定寄信；開發是 Mailpit，正式環境是專用 Gmail。"""

import smtplib
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings
from app.domain.reminder.letter import Letter


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    letter: Letter
    message_id: str


class Mailer(Protocol):
    def send(self, mail: OutgoingMail) -> None:
        """寄出一封信；失敗時丟例外（交給 Celery 重試）。"""
        ...


def to_message(mail: OutgoingMail) -> EmailMessage:
    """組成信件：純文字為主，另附 HTML 版；寄件者顯示「好險有你」。"""
    message = EmailMessage()
    message["Subject"] = mail.letter.subject
    username, _, domain = settings.MAIL_FROM.partition("@")
    message["From"] = Address(settings.MAIL_FROM_NAME, username, domain)
    message["To"] = mail.to
    message["Message-ID"] = mail.message_id
    message.set_content(mail.letter.text)
    message.add_alternative(mail.letter.html, subtype="html")
    return message


class SmtpMailer:
    """依 `SMTP_*` 設定寄信；連線與讀寫都有逾時，避免 worker 卡住。"""

    def send(self, mail: OutgoingMail) -> None:
        with smtplib.SMTP(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
        ) as smtp:
            if settings.SMTP_STARTTLS:
                smtp.starttls()
            if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                smtp.login(
                    settings.SMTP_USERNAME, settings.SMTP_PASSWORD.get_secret_value()
                )
            smtp.send_message(to_message(mail))
