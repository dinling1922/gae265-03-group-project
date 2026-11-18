"""工具註冊處：每個工具一個函式、一個檔案，放在 `tools/` 底下。

新增工具（例如 #473 `get_comparison`、#474 `list_todos`）：在 `tools/` 新增一個檔案，
用 `@mcp_tool` 裝飾一個 async 函式即可；不必改這裡或其他檔案，`register_all` 會自動載入 `tools/` 下所有模組。

規則：
- 函式名就是工具名稱；docstring 是給 AI 看的說明，用繁體中文
- 身分用 `tool_env().identity`，只查 `identity.user.id` 的資料
- 回傳專用的允許清單 pydantic 模型，不重用 API 回應模型（ADR-0032）
- 稽核由這裡統一做（只記權杖、工具名稱、時間、成功與否），工具不用管
"""

import functools
import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.mcp_server import tools
from app.mcp_server.context import tool_env
from app.models import McpCallLog

logger = logging.getLogger(__name__)

TOOLS: list[Callable[..., Awaitable[Any]]] = []

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def mcp_tool[F: Callable[..., Awaitable[Any]]](fn: F) -> F:
    """把函式登記成唯讀的 MCP 工具。"""
    TOOLS.append(fn)
    return fn


async def _audit(tool_name: str, success: bool) -> None:
    env = tool_env()
    try:
        async with env.session_maker() as session:
            session.add(
                McpCallLog(
                    token_id=env.identity.token_id,
                    tool_name=tool_name,
                    success=success,
                )
            )
            await session.commit()
    except Exception:  # 稽核寫不進去不該讓使用者的讀取失敗，但要留下痕跡
        logger.exception("MCP 稽核紀錄寫入失敗（tool=%s）", tool_name)


def _audited(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = await fn(*args, **kwargs)
        except BaseException:
            await _audit(fn.__name__, False)
            raise
        await _audit(fn.__name__, True)
        return result

    return wrapper


def load_tool_modules() -> None:
    for module in pkgutil.iter_modules(tools.__path__):
        importlib.import_module(f"{tools.__name__}.{module.name}")


def register_all(server: MCPServer) -> None:
    load_tool_modules()
    for fn in TOOLS:
        server.add_tool(_audited(fn), annotations=_READ_ONLY, structured_output=True)
