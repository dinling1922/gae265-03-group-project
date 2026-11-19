"""`/line/webhook` 端點（ADR-0033）。

掛法：和 `/mcp` 一樣用 Starlette `Route` 加進 app，而不是 FastAPI 路由：
- 不會進 OpenAPI，所以不會有 operationId 衝突，前端 client 也不必重新產生
- 這是給 LINE 平台打的端點，不是給前端用的 API，不在 `/api/v1` 底下

處理順序：沒設定頻道 → 404；用原始 body 驗 `X-Line-Signature`（HMAC-SHA256、base64、常數時間比對），
錯誤或缺少 → 401；之後一律回 200（LINE 的 Verify 請求 events 是空陣列，也回 200）。
單一事件處理出錯只記 log，不影響其他事件，也不讓 LINE 因非 200 而重送。
"""

import base64
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.core.config import settings
from app.line_bot import texts
from app.line_bot.cards.menu import reply_menu
from app.line_bot.client import HttpLineClient, LineClient, TextMessage
from app.line_bot.flex import parse_postback_data
from app.line_bot.registry import (
    EVENT_HANDLERS,
    LineContext,
    SessionMaker,
    load_command_modules,
    match_command,
    match_postback,
)
from app.models import LineEvent

logger = logging.getLogger(__name__)

LINE_WEBHOOK_PATH = "/line/webhook"
SIGNATURE_HEADER = "x-line-signature"


def sign(body: bytes, channel_secret: str) -> str:
    """LINE 的簽章：base64(HMAC-SHA256(body, channel secret))。測試也用它造簽章。"""
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_signature(body: bytes, signature: str | None, channel_secret: str) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign(body, channel_secret), signature)


def default_line_client() -> LineClient:
    return HttpLineClient(settings.LINE_CHANNEL_ACCESS_TOKEN.get_secret_value())


async def claim(session_maker: SessionMaker, key: str) -> bool:
    """登記一個事件 key；第一次回 True，已登記過（重送）回 False。靠資料庫唯一限制，多個 worker 也安全。"""
    stmt = (
        pg_insert(LineEvent)
        .values(id=uuid.uuid4(), event_key=key)
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(col(LineEvent.id))
    )
    async with session_maker() as session:
        inserted = (await session.exec(stmt)).first()
        await session.commit()
    return inserted is not None


class LineWebhook:
    def __init__(
        self,
        session_maker: SessionMaker,
        client_factory: Callable[[], LineClient] = default_line_client,
    ) -> None:
        self.session_maker = session_maker
        self.client_factory = client_factory
        load_command_modules()

    async def endpoint(self, request: Request) -> Response:
        if not settings.line_enabled:
            return JSONResponse({"error": "not_found"}, status_code=404)
        body = await request.body()
        secret = settings.LINE_CHANNEL_SECRET.get_secret_value()
        if not verify_signature(body, request.headers.get(SIGNATURE_HEADER), secret):
            return JSONResponse({"error": "invalid_signature"}, status_code=401)
        try:
            events = json.loads(body).get("events", [])
            if not isinstance(events, list):
                raise TypeError("events 不是陣列")
        except ValueError, TypeError, AttributeError:
            logger.warning("LINE webhook 的內容不是預期的 JSON，略過")
            return JSONResponse({})
        client = self.client_factory()
        for event in events:
            try:
                await self.handle_event(event, client)
            except Exception:  # 單一事件出錯不影響其他事件，也不讓 LINE 因非 200 重送
                logger.exception("LINE 事件處理失敗（type=%s）", _event_type(event))
        return JSONResponse({})

    async def handle_event(self, event: dict[str, Any], client: LineClient) -> None:
        source = event.get("source") or {}
        source_type = source.get("type")
        if source_type in ("group", "room"):
            await self._group_notice(event, source, client)
            return
        if source_type != "user":
            return
        key = event.get("webhookEventId")
        if key and not await claim(self.session_maker, f"event:{key}"):
            return  # LINE 重送：已處理過
        ctx = LineContext(
            event=event,
            line_user_id=source.get("userId") or "",
            text="",
            args="",
            session_maker=self.session_maker,
            _client=client,
        )
        event_type = event.get("type")
        if event_type == "message":
            await self._message(ctx)
        elif event_type == "follow":
            await reply_menu(ctx, welcome=True)
        elif event_type == "postback":
            await self._postback(ctx)
        # 其他事件（例如 unfollow，LINE-2 用 @line_event("unfollow") 登記）交給登記的處理器；沒有就忽略
        for handler in EVENT_HANDLERS.get(str(event_type), []):
            await handler(ctx)

    async def _message(self, ctx: LineContext) -> None:
        message = ctx.event.get("message") or {}
        if message.get("type") == "text":
            ctx.text = str(message.get("text", "")).strip()
        matched = match_command(ctx.text) if ctx.text else None
        if matched is None:
            await ctx.reply(texts.HELP_TEXT, texts.HELP_QUICK_REPLIES)
            return
        handler, ctx.args = matched
        await handler(ctx)

    async def _postback(self, ctx: LineContext) -> None:
        """卡片按鈕：`data` 是 `cmd=…&…`，對到和打字相同的指令處理器；不認得就回選單卡。"""
        params = parse_postback_data((ctx.event.get("postback") or {}).get("data"))
        matched = match_postback(params["cmd"]) if params else None
        if params is None or matched is None:
            await reply_menu(ctx)
            return
        handler, ctx.text = matched
        ctx.postback = {k: v for k, v in params.items() if k != "cmd"}
        await handler(ctx)

    async def _group_notice(
        self, event: dict[str, Any], source: dict[str, Any], client: LineClient
    ) -> None:
        """群組／聊天室：不處理任何指令，每個聊天室只在第一次被加入或被傳訊時回一次說明。"""
        if event.get("type") not in ("join", "message"):
            return
        chat_id = source.get("groupId") or source.get("roomId")
        token = event.get("replyToken")
        if not chat_id or not token:
            return
        if await claim(self.session_maker, f"notice:{chat_id}"):
            await client.reply(token, [TextMessage(texts.GROUP_NOTICE_TEXT)])


def _event_type(event: Any) -> str:
    return str(event.get("type")) if isinstance(event, dict) else "?"


def build_route(
    session_maker: SessionMaker,
    client_factory: Callable[[], LineClient] = default_line_client,
) -> Route:
    return Route(
        LINE_WEBHOOK_PATH,
        LineWebhook(session_maker, client_factory).endpoint,
        methods=["POST"],
    )
