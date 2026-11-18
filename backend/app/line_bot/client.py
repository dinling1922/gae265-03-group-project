"""和 LINE Messaging API 的對話（ADR-0033）。

呼叫端只依賴 `LineClient` 這個 Protocol：正式執行用 `HttpLineClient`，測試換成假的，不連 LINE。
只用回覆 API（回覆權杖一分鐘內有效、只能用一次），不推播。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

REPLY_URL = "https://api.line.me/v2/bot/message/reply"


@dataclass(frozen=True)
class QuickReply:
    """快速回覆按鈕：按下後以 `text` 當作使用者傳的訊息送出（message action）。"""

    label: str  # LINE 限制 20 字以內
    text: str


@dataclass(frozen=True)
class TextMessage:
    text: str
    quick_replies: Sequence[QuickReply] = ()


class LineClient(Protocol):
    async def reply(self, reply_token: str, messages: Sequence[TextMessage]) -> None:
        """用回覆權杖回覆（一次最多 5 則）。失敗時只記 log，不丟例外：webhook 不能因此回非 200。"""
        ...


def message_payload(message: TextMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "text", "text": message.text}
    if message.quick_replies:
        payload["quickReply"] = {
            "items": [
                {
                    "type": "action",
                    "action": {"type": "message", "label": q.label, "text": q.text},
                }
                for q in message.quick_replies
            ]
        }
    return payload


class HttpLineClient:
    def __init__(self, channel_access_token: str, timeout: float = 10.0) -> None:
        self._token = channel_access_token
        self._timeout = timeout

    async def reply(self, reply_token: str, messages: Sequence[TextMessage]) -> None:
        body = {
            "replyToken": reply_token,
            "messages": [message_payload(m) for m in messages],
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.post(
                    REPLY_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
            if response.status_code != 200:
                # 只記狀態碼，不記回應內文與權杖
                logger.error("LINE 回覆 API 失敗：HTTP %s", response.status_code)
        except httpx.HTTPError as exc:
            logger.error("LINE 回覆 API 連線失敗：%s", type(exc).__name__)
