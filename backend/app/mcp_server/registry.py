"""工具註冊處：每個工具一個函式、一個檔案，放在 `tools/` 底下。

新增工具（例如 #473 `get_comparison`、#474 `list_todos`）：在 `tools/` 新增一個檔案，
用 `@mcp_tool` 裝飾一個 async 函式即可；不必改這裡或其他檔案，`register_all` 會自動載入 `tools/` 下所有模組。

規則：
- 函式名就是工具名稱；docstring 是給 AI 看的說明，用繁體中文
- 身分用 `tool_env().identity`，只查 `identity.user.id` 的資料
- 回傳專用的允許清單 pydantic 模型，不重用 API 回應模型（ADR-0032）
- 稽核由這裡統一做（只記權杖、工具名稱、時間、成功與否），工具不用管

註冊參數（ADR-0034）：
- `@mcp_tool`：唯讀工具，唯讀權杖即可呼叫，只受守門層每把權杖的整體上限限制
- `@mcp_tool(scope="write")`：需要有寫入權的權杖；唯讀權杖呼叫會得到 ToolError 並記稽核為失敗
- `@mcp_tool(calls_llm=True)`：會呼叫後端 LLM；每把權杖對這個工具每分鐘的上限用設定
  `MCP_LLM_TOOL_RATE_LIMIT_PER_MINUTE`（預設 10）
- `@mcp_tool(rate_limit_per_minute=N)`：明確指定這個工具每把權杖每分鐘的上限（優先於 `calls_llm`）
- 參數可以組合，例如 `@mcp_tool(scope="write", calls_llm=True)`
- 每個工具的限流以（權杖, 工具名稱）為鍵，彼此獨立，也獨立於守門層的整體上限
- 範圍檢查與限流都在呼叫工具函式之前，被拒的呼叫不會執行工具、也不會碰資料庫
"""

import functools
import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import ceil
from typing import Any, Literal, overload

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.domain.access_tokens import SCOPE_READ, SCOPE_WRITE
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


_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,  # 寫入工具只建立或更新自己的紀錄，不刪除（ADR-0034）
    idempotent_hint=False,
    open_world_hint=False,
)

WRITE_SCOPE_REQUIRED = (
    "這個工具需要有寫入權（write）的存取權杖；請到帳號頁建立可寫入的權杖"
)


@dataclass(frozen=True)
class ToolSpec:
    scope: Literal["read", "write"] = SCOPE_READ
    rate_limit_per_minute: int | None = None  # 明確指定的上限
    calls_llm: bool = False  # 沒指定上限時，用 LLM 工具的預設上限


SPECS: dict[str, ToolSpec] = {}


@overload
def mcp_tool[F: Callable[..., Awaitable[Any]]](fn: F, /) -> F: ...
@overload
def mcp_tool[F: Callable[..., Awaitable[Any]]](
    *,
    scope: Literal["read", "write"] = SCOPE_READ,
    rate_limit_per_minute: int | None = None,
    calls_llm: bool = False,
) -> Callable[[F], F]: ...
def mcp_tool(
    fn: Callable[..., Awaitable[Any]] | None = None,
    /,
    *,
    scope: Literal["read", "write"] = SCOPE_READ,
    rate_limit_per_minute: int | None = None,
    calls_llm: bool = False,
) -> Any:
    """把函式登記成 MCP 工具；用法與參數見模組說明。"""
    spec = ToolSpec(scope, rate_limit_per_minute, calls_llm)

    def register(f: Callable[..., Awaitable[Any]]):
        TOOLS.append(f)
        SPECS[f.__name__] = spec
        return f

    return register if fn is None else register(fn)


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


def _check_scope_and_rate(name: str, spec: ToolSpec) -> None:
    """呼叫工具函式之前的檢查；不通過丟 ToolError（由 `_audited` 記稽核為失敗）。"""
    env = tool_env()
    if spec.scope not in env.identity.scopes:
        raise ToolError(WRITE_SCOPE_REQUIRED)
    limit = spec.rate_limit_per_minute
    if limit is None and spec.calls_llm:
        limit = env.llm_tool_rate_limit_per_minute
    if limit is None or env.tool_limiter is None:
        return
    wait = env.tool_limiter.check((env.identity.token_id, name), limit)
    if wait is not None:
        raise ToolError(
            f"這個工具呼叫太頻繁（每分鐘最多 {limit} 次），請約 {max(ceil(wait), 1)} 秒後再試"
        )


def _audited(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            _check_scope_and_rate(fn.__name__, SPECS.get(fn.__name__, ToolSpec()))
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
        write = SPECS.get(fn.__name__, ToolSpec()).scope == SCOPE_WRITE
        server.add_tool(
            _audited(fn),
            annotations=_WRITE if write else _READ_ONLY,
            structured_output=True,
        )
