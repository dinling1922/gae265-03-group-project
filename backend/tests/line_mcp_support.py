"""LINE 指令（比較、待辦）測試的共用工具：webhook + 假 LINE 客戶端 + 真實 MCP 守門層 + 真實測試資料庫。

用法：在測試檔 `from tests import line_mcp_support as lm`，再
`line, endpoint, rate_limit, http, chat = lm.line, lm.endpoint, lm.rate_limit, lm.http, lm.chat`
把 fixture 帶進來（和 test_mcp_get_comparison 重用 fixture 的做法相同）。
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from app.core.token_cipher import cipher_from_settings
from app.domain.line_binding import bind_line, issue_code
from app.line_bot import mcp_client
from app.line_bot.client import QuickReply, TextMessage
from app.line_bot.webhook import build_route
from app.mcp_server.endpoint import McpEndpoint, build_endpoint
from app.models import User
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.applications import Starlette

from tests.line_support import (
    FakeLineClient,
    signed_headers,
    use_line_settings,
    user_message,
    webhook_body,
)

PATH = "/line/webhook"


@pytest.fixture
def line() -> FakeLineClient:
    return FakeLineClient()


@pytest.fixture
def rate_limit() -> int:
    return 1000


@pytest.fixture
async def endpoint(engine: AsyncEngine, rate_limit: int) -> AsyncGenerator[McpEndpoint]:
    ep = build_endpoint(
        lambda: AsyncSession(engine, expire_on_commit=False),
        rate_limit_per_minute=rate_limit,
    )
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with ep.run():
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    await started.wait()
    yield ep
    stop.set()
    await task


@pytest.fixture
async def http(
    engine: AsyncEngine,
    line: FakeLineClient,
    endpoint: McpEndpoint,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient]:
    use_line_settings(monkeypatch)
    # 轉接層預設呼叫 app 的 /mcp；測試換成用測試資料庫的守門層（其餘全是真的）
    monkeypatch.setattr(mcp_client, "_default_asgi", lambda: endpoint.asgi)
    app = Starlette(
        routes=[
            build_route(
                lambda: AsyncSession(engine, expire_on_commit=False), lambda: line
            )
        ]
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


class Chat:
    """替某個 LINE userId 傳訊息，每則用新的事件 id；回傳這一則得到的回覆（文字、快速回覆）。"""

    def __init__(self, http: AsyncClient, line: FakeLineClient, user_id: str) -> None:
        self.http, self.line, self.user_id, self.n = http, line, user_id, 0

    async def send(self, text: str) -> TextMessage:
        self.n += 1
        body = webhook_body(
            user_message(
                text, event_id=f"{self.user_id}-{self.n}", user_id=self.user_id
            )
        )
        before = len(self.line.replies)
        resp = await self.http.post(PATH, content=body, headers=signed_headers(body))
        assert resp.status_code == 200
        new = self.line.replies[before:]
        assert len(new) == 1 and len(new[0][1]) == 1
        return new[0][1][0]

    async def say(self, text: str) -> str:
        return (await self.send(text)).text


@pytest.fixture
def chat(http: AsyncClient, line: FakeLineClient) -> Chat:
    return Chat(http, line, "U1")


async def make_bound_user(
    session: AsyncSession, email: str = "a@example.com", line_user_id: str = "U1"
) -> User:
    """建立使用者並把 LINE userId 綁到他（走真實的綁定流程）。"""
    user = User(email=email)
    session.add(user)
    await session.commit()
    code, _ = await issue_code(session, user.id)
    await bind_line(session, line_user_id, code, cipher_from_settings())
    return user


def quick_texts(message: TextMessage) -> list[str]:
    return [q.text for q in message.quick_replies]


def assert_line_limits(message: TextMessage) -> None:
    """LINE 的限制：單則 5000 字、快速回覆最多 13 個且 label 20 字內。"""
    assert len(message.text) <= 5000
    assert len(message.quick_replies) <= 13
    for q in message.quick_replies:
        assert isinstance(q, QuickReply) and 0 < len(q.label) <= 20


def new_id() -> str:
    return str(uuid.uuid4())
