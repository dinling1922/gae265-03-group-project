"""MCP 工具的權限範圍與每工具限流（ADR-0034、#507）。

這張票不新增真實工具：在測試裡臨時把假工具登記進 `TOOLS`／`SPECS`，建完 endpoint 就還原，
不影響其他測試。其餘（守門層、稽核、資料庫）全是真的。
"""

import asyncio
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from app.core.config import settings
from app.domain.access_tokens import create_token
from app.mcp_server import registry
from app.mcp_server.endpoint import McpEndpoint, build_endpoint
from app.mcp_server.registry import WRITE_SCOPE_REQUIRED, mcp_tool
from app.models import McpCallLog, User
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

URL = "http://test/mcp"
executed: list[str] = []


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture(autouse=True)
def reset_executed() -> None:
    executed.clear()


@pytest.fixture
def fake_tools() -> Iterator[None]:
    """登記四個假工具，測完還原（TOOLS 與 SPECS 是全域的）。"""
    tools_before = list(registry.TOOLS)
    specs_before = dict(registry.SPECS)

    @mcp_tool
    async def fake_read_tool() -> dict[str, str]:
        """假的唯讀工具。"""
        executed.append("fake_read_tool")
        return {"ok": "read"}

    @mcp_tool(scope="write")
    async def fake_write_tool() -> dict[str, str]:
        """假的寫入工具。"""
        executed.append("fake_write_tool")
        return {"ok": "write"}

    @mcp_tool(calls_llm=True)
    async def fake_llm_tool() -> dict[str, str]:
        """假的會呼叫 LLM 的唯讀工具。"""
        executed.append("fake_llm_tool")
        return {"ok": "llm"}

    @mcp_tool(scope="write", rate_limit_per_minute=2)
    async def fake_limited_write_tool() -> dict[str, str]:
        """假的明確指定上限的寫入工具。"""
        executed.append("fake_limited_write_tool")
        return {"ok": "limited"}

    yield
    registry.TOOLS[:] = tools_before
    registry.SPECS.clear()
    registry.SPECS.update(specs_before)


@pytest.fixture
def llm_limit() -> int | None:
    return 3


@pytest.fixture
async def endpoint(
    engine: AsyncEngine,
    clock: Clock,
    llm_limit: int | None,
    fake_tools: None,  # noqa: ARG001  先登記假工具再建 endpoint
) -> AsyncGenerator[McpEndpoint]:
    ep = build_endpoint(
        lambda: AsyncSession(engine, expire_on_commit=False),
        rate_limit_per_minute=1000,
        clock=clock,
        llm_tool_rate_limit_per_minute=llm_limit,
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


async def make_token(
    session: AsyncSession, email: str, scopes: tuple[str, ...] = ("read",)
) -> str:
    user = User(email=email)
    session.add(user)
    await session.commit()
    _, plaintext = await create_token(session, user.id, "測試", scopes)
    return plaintext


@asynccontextmanager
async def mcp_client(endpoint: McpEndpoint, token: str) -> AsyncGenerator[Client]:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=endpoint.asgi),
        headers={"Authorization": f"Bearer {token}"},
    ) as http:
        async with Client(streamable_http_client(URL, http_client=http)) as client:
            yield client


async def logs(session: AsyncSession, tool: str) -> list[bool]:
    rows = (
        await session.exec(
            select(McpCallLog)
            .where(McpCallLog.tool_name == tool)
            .order_by(McpCallLog.created_at)  # pyright: ignore[reportArgumentType]
        )
    ).all()
    return [r.success for r in rows]


# ---- 範圍 ----


async def test_read_token_is_refused_by_write_tool_and_audited_as_failure(
    session: AsyncSession, endpoint: McpEndpoint
):
    token = await make_token(session, "a@example.com")
    async with mcp_client(endpoint, token) as client:
        result = await client.call_tool("fake_write_tool", {})
    assert result.is_error
    assert WRITE_SCOPE_REQUIRED in result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]
    assert executed == []  # 工具函式根本沒執行
    assert await logs(session, "fake_write_tool") == [False]


async def test_write_token_can_call_write_tool(
    session: AsyncSession, endpoint: McpEndpoint
):
    token = await make_token(session, "a@example.com", ("read", "write"))
    async with mcp_client(endpoint, token) as client:
        result = await client.call_tool("fake_write_tool", {})
    assert not result.is_error
    assert result.structured_content == {"ok": "write"}
    assert executed == ["fake_write_tool"]
    assert await logs(session, "fake_write_tool") == [True]


@pytest.mark.parametrize("scopes", [("read",), ("read", "write")])
async def test_both_token_kinds_can_call_read_tools(
    session: AsyncSession, endpoint: McpEndpoint, scopes: tuple[str, ...]
):
    token = await make_token(session, "a@example.com", scopes)
    async with mcp_client(endpoint, token) as client:
        result = await client.call_tool("fake_read_tool", {})
    assert not result.is_error and result.structured_content == {"ok": "read"}


async def test_tool_annotations_follow_scope(
    session: AsyncSession, endpoint: McpEndpoint
):
    token = await make_token(session, "a@example.com", ("read", "write"))
    async with mcp_client(endpoint, token) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["fake_read_tool"].annotations
    assert tools["fake_read_tool"].annotations.read_only_hint is True
    assert tools["fake_write_tool"].annotations
    assert tools["fake_write_tool"].annotations.read_only_hint is False
    assert tools["fake_write_tool"].annotations.destructive_hint is False


# ---- 每工具限流 ----


async def test_llm_tool_limit_is_per_tool_and_audited(
    session: AsyncSession, endpoint: McpEndpoint, clock: Clock
):
    token = await make_token(session, "a@example.com")
    async with mcp_client(endpoint, token) as client:
        for _ in range(3):  # 設定的上限
            assert not (await client.call_tool("fake_llm_tool", {})).is_error
        blocked = await client.call_tool("fake_llm_tool", {})
        assert blocked.is_error
        text = blocked.content[0].text  # pyright: ignore[reportAttributeAccessIssue]
        assert "每分鐘最多 3 次" in text and "再試" in text
        assert executed.count("fake_llm_tool") == 3  # 被擋的沒執行
        # 其他工具（沒有工具上限）不受影響
        for _ in range(5):
            assert not (await client.call_tool("fake_read_tool", {})).is_error
        # 視窗過了就恢復
        clock.now += 61
        assert not (await client.call_tool("fake_llm_tool", {})).is_error
    assert await logs(session, "fake_llm_tool") == [True] * 3 + [False, True]


async def test_limit_is_independent_per_token(
    session: AsyncSession, endpoint: McpEndpoint
):
    alice = await make_token(session, "a@example.com")
    bob = await make_token(session, "b@example.com")
    async with mcp_client(endpoint, alice) as a:
        for _ in range(3):
            await a.call_tool("fake_llm_tool", {})
        assert (await a.call_tool("fake_llm_tool", {})).is_error
    async with mcp_client(endpoint, bob) as b:
        assert not (await b.call_tool("fake_llm_tool", {})).is_error


async def test_explicit_limit_overrides_default_and_limits_are_independent(
    session: AsyncSession, endpoint: McpEndpoint
):
    token = await make_token(session, "a@example.com", ("read", "write"))
    async with mcp_client(endpoint, token) as client:
        for _ in range(2):  # 明確上限 2，不是 LLM 預設的 3
            assert not (await client.call_tool("fake_limited_write_tool", {})).is_error
        assert (await client.call_tool("fake_limited_write_tool", {})).is_error
        # 另一個寫入工具有自己的額度
        assert not (await client.call_tool("fake_write_tool", {})).is_error
        assert not (await client.call_tool("fake_llm_tool", {})).is_error


async def test_scope_refusal_does_not_consume_the_tool_quota(
    session: AsyncSession, endpoint: McpEndpoint
):
    read_token = await make_token(session, "a@example.com")
    async with mcp_client(endpoint, read_token) as client:
        for _ in range(5):  # 範圍不足的呼叫一律是範圍錯誤，不是限流錯誤
            result = await client.call_tool("fake_limited_write_tool", {})
            assert WRITE_SCOPE_REQUIRED in result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("llm_limit", [None])
async def test_llm_limit_defaults_to_setting(
    session: AsyncSession, endpoint: McpEndpoint
):
    assert settings.MCP_LLM_TOOL_RATE_LIMIT_PER_MINUTE == 10
    token = await make_token(session, "a@example.com")
    async with mcp_client(endpoint, token) as client:
        for _ in range(10):
            assert not (await client.call_tool("fake_llm_tool", {})).is_error
        assert (await client.call_tool("fake_llm_tool", {})).is_error


# ---- 假工具不外洩 ----


@pytest.mark.usefixtures("fake_tools")
def test_fake_tools_do_not_leak_into_the_global_registry():
    assert "fake_write_tool" in registry.SPECS


def test_registry_is_clean_after_fixture_teardown():
    assert "fake_write_tool" not in registry.SPECS
    assert all(t.__name__ != "fake_write_tool" for t in registry.TOOLS)


def test_real_tools_default_to_read_scope_and_no_tool_limit():
    # 只看原本的唯讀工具；之後新增的寫入或 LLM 工具各自的測試檔鎖自己的註冊參數
    registry.load_tool_modules()
    for name in ("list_comparisons", "get_comparison", "list_todos"):
        spec = registry.SPECS[name]
        assert spec.scope == "read" and spec.rate_limit_per_minute is None, name
