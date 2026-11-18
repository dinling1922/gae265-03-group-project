"""LINE bot 測試的共用工具（ADR-0033）：測試設定、假的 LINE 客戶端、簽章。不連 LINE。"""

import json
from collections.abc import Sequence
from typing import Any

import pytest
from app.core.config import settings
from app.line_bot.client import TextMessage
from app.line_bot.webhook import sign
from pydantic import SecretStr

CHANNEL_SECRET = "test-channel-secret"
ACCESS_TOKEN = "test-channel-access-token"


def use_line_settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    """讓全域 settings 用測試的 LINE 設定，不受本機 .env 影響。"""
    values = {
        "LINE_CHANNEL_SECRET": SecretStr(CHANNEL_SECRET),
        "LINE_CHANNEL_ACCESS_TOKEN": SecretStr(ACCESS_TOKEN),
        **overrides,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


class FakeLineClient:
    """假的 LINE：記下送出的回覆（reply_token、訊息）。"""

    def __init__(self) -> None:
        self.replies: list[tuple[str, list[TextMessage]]] = []

    async def reply(self, reply_token: str, messages: Sequence[TextMessage]) -> None:
        self.replies.append((reply_token, list(messages)))

    @property
    def texts(self) -> list[str]:
        return [m.text for _, messages in self.replies for m in messages]


def webhook_body(*events: dict[str, Any]) -> bytes:
    return json.dumps({"destination": "Uxxxx", "events": list(events)}).encode()


def signed_headers(body: bytes, secret: str = CHANNEL_SECRET) -> dict[str, str]:
    return {"X-Line-Signature": sign(body, secret), "Content-Type": "application/json"}


def user_message(text: str, *, event_id: str, user_id: str = "U1") -> dict[str, Any]:
    return {
        "type": "message",
        "webhookEventId": event_id,
        "replyToken": f"token-{event_id}",
        "source": {"type": "user", "userId": user_id},
        "message": {"type": "text", "id": "1", "text": text},
    }


def follow_event(*, event_id: str, user_id: str = "U1") -> dict[str, Any]:
    return {
        "type": "follow",
        "webhookEventId": event_id,
        "replyToken": f"token-{event_id}",
        "source": {"type": "user", "userId": user_id},
    }


def group_message(
    text: str, *, event_id: str, kind: str = "group", chat_id: str = "C1"
) -> dict[str, Any]:
    source = {"type": kind, "userId": "U1", f"{kind}Id": chat_id}
    return {
        "type": "message",
        "webhookEventId": event_id,
        "replyToken": f"token-{event_id}",
        "source": source,
        "message": {"type": "text", "id": "1", "text": text},
    }
