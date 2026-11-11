"""真的透過 SMTP 寄提醒信（ADR-0028）。

- Mailpit：用正式環境同一個寄信器寄到 compose 的 Mailpit，再用 Mailpit 的 API 讀回來。
  Mailpit 沒開時自動略過（`make up` 會開）。
- Azure：只有手動才跑，會真的寄出一封信。先把 `.env` 的 SMTP 設定換成 Azure，再執行
  `make test-mail to=<你的信箱>`；沒有指定收件人時略過，CI 也不會跑。
"""

import os
import socket
import uuid

import httpx
import pytest
from app.core.config import settings
from app.domain.reminder.letter import (
    LetterInput,
    MissingGroup,
    build_letter,
    message_id,
)
from app.domain.reminder.mailer import OutgoingMail, SmtpMailer
from app.models.base import utc_now

MAILPIT_HOST = os.environ.get("MAILPIT_HOST", "localhost")
MAILPIT_SMTP_PORT = int(os.environ.get("MAILPIT_SMTP_PORT", "1025"))
MAILPIT_API = f"http://{MAILPIT_HOST}:{os.environ.get('MAILPIT_PORT', '8025')}/api/v1"
LIVE_TO = os.environ.get("REMINDER_LIVE_TO")


def mailpit_running() -> bool:
    try:
        with socket.create_connection((MAILPIT_HOST, MAILPIT_SMTP_PORT), timeout=1):
            return True
    except OSError:
        return False


def sample_mail(to: str) -> OutgoingMail:
    letter = build_letter(
        LetterInput(
            user_name="王小明",
            trip_name="日本 2026/07/05～07/10",
            groups=[
                MissingGroup(
                    label="班機取消 · 2026/07/10",
                    documents=["航空公司開立的班機取消證明", "住宿收據"],
                )
            ],
            company="富邦產物",
            upload_url="https://example.com/incident/abc",
        )
    )
    return OutgoingMail(
        to=to,
        letter=letter,
        message_id=message_id(uuid.uuid4(), utc_now(), 1, settings.MAIL_FROM),
    )


@pytest.mark.skipif(not mailpit_running(), reason="Mailpit 沒開（make up）")
def test_透過_SMTP_寄到_Mailpit_主旨_寄件者與_Message_ID_都正確(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", MAILPIT_HOST)
    monkeypatch.setattr(settings, "SMTP_PORT", MAILPIT_SMTP_PORT)
    monkeypatch.setattr(settings, "SMTP_STARTTLS", False)
    monkeypatch.setattr(settings, "SMTP_USERNAME", None)
    monkeypatch.setattr(settings, "MAIL_FROM", "no-reply@example.com")
    mail = sample_mail("traveler@example.com")

    SmtpMailer().send(mail)

    found = httpx.get(
        f"{MAILPIT_API}/search", params={"query": f"message-id:{mail.message_id}"}
    ).json()["messages"]
    assert len(found) == 1
    detail = httpx.get(f"{MAILPIT_API}/message/{found[0]['ID']}").json()
    assert detail["Subject"] == "【好險有你】日本 2026/07/05～07/10 還缺 2 份理賠文件"
    assert detail["From"] == {"Name": "好險有你", "Address": "no-reply@example.com"}
    assert [t["Address"] for t in detail["To"]] == ["traveler@example.com"]
    assert f"<{detail['MessageID']}>" == mail.message_id
    assert "・航空公司開立的班機取消證明" in detail["Text"]
    assert ">前往上傳</a>" in detail["HTML"]


@pytest.mark.skipif(LIVE_TO is None, reason="手動測試：make test-mail to=<信箱>")
def test_透過正式的_SMTP_設定真的寄出一封() -> None:
    assert LIVE_TO is not None
    SmtpMailer().send(sample_mail(LIVE_TO))
