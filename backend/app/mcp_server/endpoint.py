"""`/mcp` 端點：官方 SDK 的 streamable HTTP，外面包一層只認 Bearer 的 ASGI 守門層。

守門層順序：Bearer → `authenticate_bearer`（撤銷與到期每次都查）→ scope 含 read → 每把權杖限流 → SDK。
- 只看 `Authorization` 標頭，完全不讀 cookie，所以 cookie session 在這條路徑沒有作用
- 不開 CORS（不加 CORSMiddleware，也不回 Access-Control 標頭）
- 無權杖、壞權杖、過期、撤銷都回同樣的 401，不洩漏是哪一種
- stateless：每個請求獨立驗證身分，不留 SDK session，多個 worker 也不需要黏著
"""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from math import ceil

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.domain.access_tokens import SCOPE_READ, authenticate_bearer
from app.mcp_server.context import (
    SessionMaker,
    ToolEnv,
    reset_tool_env,
    set_tool_env,
)
from app.mcp_server.ratelimit import RateLimiter
from app.mcp_server.registry import register_all

MCP_PATH = "/mcp"

_UNAUTHORIZED = {"error": "unauthorized", "message": "需要有效的存取權杖"}


def _bearer(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


class BearerGate:
    def __init__(
        self, inner: ASGIApp, session_maker: SessionMaker, limiter: RateLimiter
    ) -> None:
        self.inner = inner
        self.session_maker = session_maker
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        bearer = _bearer(scope)
        identity = None
        if bearer is not None:
            async with self.session_maker() as session:
                identity = await authenticate_bearer(session, bearer)
        if identity is None:
            response = JSONResponse(
                _UNAUTHORIZED,
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return await response(scope, receive, send)
        if SCOPE_READ not in identity.scopes:
            response = JSONResponse(
                {"error": "insufficient_scope", "message": "權杖沒有 read 權限"},
                status_code=403,
            )
            return await response(scope, receive, send)
        wait = self.limiter.check(identity.token_id)
        if wait is not None:
            response = JSONResponse(
                {"error": "rate_limited", "message": "呼叫太頻繁，請稍後再試"},
                status_code=429,
                headers={"Retry-After": str(max(ceil(wait), 1))},
            )
            return await response(scope, receive, send)
        token = set_tool_env(ToolEnv(identity, self.session_maker))
        try:
            await self.inner(scope, receive, send)
        finally:
            reset_tool_env(token)


@dataclass
class McpEndpoint:
    asgi: ASGIApp
    server: MCPServer

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """啟動 SDK 的 session manager；一個 endpoint 只能啟動一次，要放在 FastAPI 的 lifespan。"""
        async with self.server.session_manager.run():
            yield


def build_endpoint(
    session_maker: SessionMaker,
    rate_limit_per_minute: int,
    clock: Callable[[], float] = time.monotonic,
) -> McpEndpoint:
    server = MCPServer(
        "好險有你",
        instructions="好險有你的唯讀資料查詢：只能讀取權杖擁有者自己的資料。",
    )
    register_all(server)
    sdk_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        # DNS rebinding 防護針對「沒有認證的本機服務」；這裡每個請求都要 Bearer，
        # 瀏覽器不會自動帶，所以關掉，否則預設只放行 localhost 的 Host 而擋掉正式網域
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    gate = BearerGate(
        sdk_app, session_maker, RateLimiter(rate_limit_per_minute, clock=clock)
    )
    return McpEndpoint(asgi=gate, server=server)
