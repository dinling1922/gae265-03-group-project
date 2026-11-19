"""工具執行時的環境：呼叫者身分與資料庫 session 工廠。

身分由 `/mcp` 的 ASGI 守門層驗證 Bearer 後放進 contextvar，工具只透過 `tool_env()` 取得，
不碰 HTTP 細節；日後改用 OAuth 2.1 或 LINE 綁定只需換守門層（ADR-0032）。
"""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass

from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.access_tokens import TokenIdentity
from app.mcp_server.ratelimit import RateLimiter

SessionMaker = Callable[[], AsyncSession]


@dataclass(frozen=True)
class ToolEnv:
    identity: TokenIdentity
    session_maker: SessionMaker
    # 每個工具各自的限流（鍵是權杖加工具名稱）與呼叫後端 LLM 的工具的預設上限；
    # 沒給（None）就不做工具層限流，只剩守門層每把權杖的整體上限
    tool_limiter: RateLimiter | None = None
    llm_tool_rate_limit_per_minute: int = 10


_env: ContextVar[ToolEnv | None] = ContextVar("mcp_tool_env", default=None)


def set_tool_env(env: ToolEnv | None):
    return _env.set(env)


def reset_tool_env(token) -> None:
    _env.reset(token)


def tool_env() -> ToolEnv:
    env = _env.get()
    if env is None:  # 守門層一定會設；沒有代表有人繞過它
        raise RuntimeError("MCP 工具在沒有驗證身分的情況下被呼叫")
    return env
