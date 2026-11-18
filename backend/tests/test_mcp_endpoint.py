"""MCP 端點（ADR-0032、#472）：/mcp 的 Bearer 驗證、限流、稽核與 list_comparisons。

用 SDK 的 MCP 客戶端經由 ASGI（不開 port）連到真實的 /mcp 守門層，資料庫是測試資料庫。
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from app.api.routes.auth import dev_router
from app.core.config import settings
from app.domain.access_tokens import create_token
from app.main import app as main_app
from app.mcp_server.endpoint import McpEndpoint, build_endpoint
from app.models import ApiToken, Comparison, McpCallLog, User
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route

URL = "http://test/mcp"
ALLOWED_KEYS = {
    "id",
    "destination",
    "depart_date",
    "return_date",
    "days",
    "has_recommendation",
    "created_at",
    "updated_at",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def rate_limit() -> int:
    return 1000  # 限流測試另外覆寫


@pytest.fixture
async def endpoint(
    engine: AsyncEngine, clock: Clock, rate_limit: int
) -> AsyncGenerator[McpEndpoint]:
    ep = build_endpoint(
        lambda: AsyncSession(engine, expire_on_commit=False),
        rate_limit_per_minute=rate_limit,
        clock=clock,
    )
    # anyio 的 task group 必須在同一個 task 進出，pytest 的 fixture 設定與收尾不是同一個 task，
    # 所以在專用 task 裡啟動 session manager
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


async def make_user(session: AsyncSession, email: str) -> User:
    user = User(email=email)
    session.add(user)
    await session.commit()
    return user


async def make_token(session: AsyncSession, user: User) -> str:
    _, plaintext = await create_token(session, user.id, "測試")
    return plaintext


@asynccontextmanager
async def mcp_client(endpoint: McpEndpoint, token: str) -> AsyncGenerator[Client]:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=endpoint.asgi),
        headers={"Authorization": f"Bearer {token}"},
    ) as http:
        async with Client(streamable_http_client(URL, http_client=http)) as client:
            yield client


async def call(client: Client, **args: object) -> dict:
    result = await client.call_tool("list_comparisons", args)
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


async def raw_post(
    endpoint: McpEndpoint, headers: dict[str, str] | None = None
) -> httpx2.Response:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=endpoint.asgi)
    ) as http:
        return await http.post(
            URL,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                **(headers or {}),
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )


def comparison(user: User, destination: str = "日本", **kw: Any) -> Comparison:
    fields: dict = {
        "user_id": user.id,
        "destination": destination,
        "depart_date": date(2026, 12, 1),
        "return_date": date(2026, 12, 5),
    }
    return Comparison(**(fields | kw))


# ---- 成功路徑 ----


async def test_lists_own_comparisons_newest_first(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    now = datetime.now(UTC)
    for i, dest in enumerate(["日本", "韓國", "泰國"]):
        session.add(comparison(user, dest, created_at=now + timedelta(minutes=i)))
    await session.commit()
    token = await make_token(session, user)

    async with mcp_client(endpoint, token) as client:
        tools = await client.list_tools()
        assert "list_comparisons" in [t.name for t in tools.tools]
        assert tools.tools[0].description and "比較紀錄" in tools.tools[0].description
        out = await call(client)

    assert [c["destination"] for c in out["comparisons"]] == ["泰國", "韓國", "日本"]
    assert out["comparisons"][0]["days"] == 5
    assert out["comparisons"][0]["has_recommendation"] is False


async def test_has_recommendation_reflects_saved_plans(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    session.add(
        comparison(
            user,
            recommended_plan_code="p1",
            recommended_sum_insured=1_000_000,
            alternative_plan_code="p2",
            alternative_sum_insured=1_000_000,
        )
    )
    await session.commit()
    token = await make_token(session, user)
    async with mcp_client(endpoint, token) as client:
        out = await call(client)
    assert out["comparisons"][0]["has_recommendation"] is True


async def test_only_returns_callers_comparisons(
    session: AsyncSession, endpoint: McpEndpoint
):
    alice = await make_user(session, "a@example.com")
    bob = await make_user(session, "b@example.com")
    session.add(comparison(alice, "日本"))
    session.add(comparison(bob, "韓國"))
    await session.commit()
    alice_token = await make_token(session, alice)
    bob_token = await make_token(session, bob)

    async with mcp_client(endpoint, alice_token) as client:
        out = await call(client)
    assert [c["destination"] for c in out["comparisons"]] == ["日本"]
    async with mcp_client(endpoint, bob_token) as client:
        out = await call(client)
    assert [c["destination"] for c in out["comparisons"]] == ["韓國"]


async def test_output_fields_are_an_allowlist(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    session.add(
        comparison(
            user,
            city="東京澀谷",
            concerns=["醫療"],
            concern_suggestions=[{"concern": "醫療", "reason": "自由文字"}],
        )
    )
    await session.commit()
    token = await make_token(session, user)
    async with mcp_client(endpoint, token) as client:
        out = await call(client)
        raw = await client.call_tool("list_comparisons", {})

    assert set(out) == {"comparisons"}
    assert set(out["comparisons"][0]) == ALLOWED_KEYS
    text = str(raw)
    assert "東京澀谷" not in text and "自由文字" not in text
    assert "a@example.com" not in text


# ---- limit ----


async def test_limit_bounds(session: AsyncSession, endpoint: McpEndpoint):
    user = await make_user(session, "a@example.com")
    for _ in range(12):
        session.add(comparison(user))
    await session.commit()
    token = await make_token(session, user)

    async with mcp_client(endpoint, token) as client:
        assert len((await call(client))["comparisons"]) == 10  # 預設
        assert len((await call(client, limit=3))["comparisons"]) == 3
        assert len((await call(client, limit=50))["comparisons"]) == 12
        for bad in (0, -1, 51):
            result = await client.call_tool("list_comparisons", {"limit": bad})
            assert result.is_error  # 超出範圍直接拒絕，不夾住
            assert "limit" in str(result.content)


# ---- 認證 ----


async def test_no_token_rejected(endpoint: McpEndpoint):
    resp = await raw_post(endpoint)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_bad_token_rejected(endpoint: McpEndpoint):
    for header in ("Bearer hyi_nope", "Bearer ", "Basic abc", "hyi_nope"):
        resp = await raw_post(endpoint, {"Authorization": header})
        assert resp.status_code == 401, header


async def test_expired_and_revoked_rejected_with_same_response(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    expired = await make_token(session, user)
    revoked = await make_token(session, user)
    rows = (await session.exec(select(ApiToken))).all()
    from app.domain.access_tokens import hash_token

    by_hash = {r.token_hash: r for r in rows}
    by_hash[hash_token(expired)].expires_at = datetime.now(UTC) - timedelta(days=1)
    by_hash[hash_token(revoked)].revoked_at = datetime.now(UTC)
    await session.commit()

    bodies = []
    for token in (expired, revoked, "hyi_unknown"):
        resp = await raw_post(endpoint, {"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        bodies.append(resp.text)
    assert len(set(bodies)) == 1  # 不洩漏是哪一種


async def test_revocation_takes_effect_on_next_call(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    token = await make_token(session, user)
    async with mcp_client(endpoint, token) as client:
        await call(client)
        row = (await session.exec(select(ApiToken))).one()
        row.revoked_at = datetime.now(UTC)
        await session.commit()
        with pytest.raises(Exception):  # noqa: B017, PT011 - 傳輸層錯誤型別不是這裡要鎖的
            await call(client)


async def test_token_without_read_scope_is_forbidden(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    token = await make_token(session, user)
    row = (await session.exec(select(ApiToken))).one()
    row.scopes = ["write"]
    await session.commit()
    resp = await raw_post(endpoint, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_cookie_session_is_not_accepted(
    engine: AsyncEngine, endpoint: McpEndpoint
):
    """帶有效登入 cookie、沒有 Bearer：被拒。路由掛法與 main.py 相同。"""
    web = FastAPI()
    web.add_middleware(SessionMiddleware, secret_key="test-secret", same_site="lax")
    web.include_router(dev_router, prefix=settings.API_V1_STR)
    web.router.routes.append(Route("/mcp", endpoint.asgi))

    async def override() -> AsyncGenerator[AsyncSession]:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    from app.core.db import get_session

    web.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=web), base_url="http://test"
    ) as http:
        login = await http.post(
            f"{settings.API_V1_STR}/auth/dev-login", json={"email": "a@example.com"}
        )
        assert login.status_code == 200 and http.cookies
        resp = await http.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert resp.status_code == 401


async def test_main_app_mcp_route_requires_bearer_and_has_no_cors():
    async with AsyncClient(
        transport=ASGITransport(app=main_app), base_url="http://test"
    ) as http:
        resp = await http.post(
            "/mcp",
            headers={"Origin": "https://evil.example"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 401
        assert "access-control-allow-origin" not in resp.headers
        preflight = await http.options(
            "/mcp",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in preflight.headers
        spec = (await http.get(f"{settings.API_V1_STR}/openapi.json")).json()
        assert "/mcp" not in spec["paths"]


# ---- 限流 ----


@pytest.mark.parametrize("rate_limit", [5])
async def test_rate_limit_per_token(
    session: AsyncSession, endpoint: McpEndpoint, clock: Clock
):
    alice = await make_user(session, "a@example.com")
    bob = await make_user(session, "b@example.com")
    alice_token = await make_token(session, alice)
    bob_token = await make_token(session, bob)
    headers = {"Authorization": f"Bearer {alice_token}"}

    for _ in range(5):  # 設定的上限
        assert (await raw_post(endpoint, headers)).status_code == 200
    resp = await raw_post(endpoint, headers)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1

    # 別把權杖不受影響
    other = await raw_post(endpoint, {"Authorization": f"Bearer {bob_token}"})
    assert other.status_code == 200
    # 視窗過了就恢復
    clock.now += 61
    assert (await raw_post(endpoint, headers)).status_code == 200


# ---- 稽核 ----


async def test_audit_records_calls_without_content(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    session.add(comparison(user, "日本", city="東京澀谷"))
    await session.commit()
    token = await make_token(session, user)
    token_row = (await session.exec(select(ApiToken))).one()

    async with mcp_client(endpoint, token) as client:
        await call(client, limit=3)
        bad = await client.call_tool("list_comparisons", {"limit": 999})
        assert bad.is_error

    logs = (await session.exec(select(McpCallLog).order_by("created_at"))).all()
    # 參數驗證失敗發生在工具函式之前，不算一次工具呼叫；成功的那次有紀錄
    assert [(log.token_id, log.tool_name, log.success) for log in logs] == [
        (token_row.id, "list_comparisons", True)
    ]
    # 紀錄只有這幾個欄位，沒有輸入輸出
    assert set(McpCallLog.model_fields) == {
        "id",
        "created_at",
        "updated_at",
        "token_id",
        "tool_name",
        "success",
    }


async def test_audit_records_failure(
    session: AsyncSession,
    endpoint: McpEndpoint,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.mcp_server.tools import list_comparisons as mod

    user = await make_user(session, "a@example.com")
    token = await make_token(session, user)
    token_row = (await session.exec(select(ApiToken))).one()

    class Boom(Exception):
        pass

    def broken(*_a: object, **_k: object) -> Callable:
        raise Boom

    monkeypatch.setattr(mod, "select", broken)
    async with mcp_client(endpoint, token) as client:
        result = await client.call_tool("list_comparisons", {})
        assert result.is_error

    logs = (await session.exec(select(McpCallLog))).all()
    assert [(log.token_id, log.success) for log in logs] == [(token_row.id, False)]
