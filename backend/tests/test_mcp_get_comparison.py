"""MCP get_comparison（#473）：推薦與網頁一致、incomplete、找不到、輸出允許清單。"""

import json

import pytest
from app.api.deps import current_user
from app.ingest.plan_matrix import read_plan_data
from app.main import app
from app.mcp_server.endpoint import McpEndpoint
from app.mcp_server.tools.get_comparison import NOT_FOUND
from app.models import Comparison, ItineraryActivity, McpCallLog, User
from app.seed import load_plan_data
from httpx import AsyncClient
from mcp import Client
from mcp.types import CallToolResult
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import test_mcp_endpoint as base
from tests.test_comparison_recommendation_api import trip

# 重用 test_mcp_endpoint 的 MCP 端點 fixture
endpoint, clock, rate_limit = base.endpoint, base.clock, base.rate_limit
make_user, make_token, mcp_client, comparison = (
    base.make_user,
    base.make_token,
    base.mcp_client,
    base.comparison,
)

TOP_KEYS = {
    "id",
    "status",
    "missing",
    "trip",
    "concerns",
    "recommended",
    "alternative",
}
TRIP_KEYS = {"destination", "depart_date", "return_date", "days"}
PLAN_KEYS = {"plan_id", "name", "company", "sum_insured", "premium"}


@pytest.fixture
async def seed(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())


async def get(client: Client, comparison_id: object) -> CallToolResult:
    return await client.call_tool(
        "get_comparison", {"comparison_id": str(comparison_id)}
    )


async def ok(client: Client, comparison_id: object) -> dict:
    result = await get(client, comparison_id)
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.usefixtures("seed")
async def test_complete_matches_web_api(
    session: AsyncSession, client: AsyncClient, endpoint: McpEndpoint
):
    """推薦方案、保額、保費與網頁 GET /comparisons/{id} 同一套來源、同一個數字。"""
    user = await make_user(session, "a@example.com")
    app.dependency_overrides[current_user] = lambda: user
    try:
        created = await client.post(
            "/comparisons", json=trip(6, ["班機延誤", "突然生病就醫"], city="東京澀谷")
        )
        assert created.status_code == 201, created.text
        web = (await client.get(f"/comparisons/{created.json()['id']}")).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    token = await make_token(session, user)

    async with mcp_client(endpoint, token) as c:
        out = await ok(c, web["id"])

    assert out["status"] == "complete" and out["missing"] is None
    assert out["id"] == web["id"]
    assert out["concerns"] == ["班機延誤", "突然生病就醫"]
    assert out["trip"] == {
        "destination": "日本",
        "depart_date": web["depart_date"],
        "return_date": web["return_date"],
        "days": 6,
    }
    for key in ("recommended", "alternative"):
        card = web["recommendation"][key]
        assert out[key] == {
            "plan_id": card["plan_code"],
            "name": card["name"],
            "company": card["company"],
            "sum_insured": card["sum_insured"],
            "premium": card["premium"],
        }
    # 與推薦規則測試鎖住的數字相同（test_comparison_recommendation_api）
    assert (out["recommended"]["premium"], out["alternative"]["premium"]) == (985, 429)


@pytest.mark.usefixtures("seed")
async def test_uses_saved_card_sum_insured(
    session: AsyncSession, client: AsyncClient, endpoint: McpEndpoint
):
    """使用者在網頁改了卡片保額，MCP 回的是改過的保額與對應保費。"""
    user = await make_user(session, "a@example.com")
    app.dependency_overrides[current_user] = lambda: user
    try:
        body = (await client.post("/comparisons", json=trip(6, ["班機延誤"]))).json()
        option = body["recommendation"]["recommended"]["sum_insured_options"][-1]
        patched = (
            await client.patch(
                f"/comparisons/{body['id']}", json={"recommended_sum_insured": option}
            )
        ).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    token = await make_token(session, user)
    async with mcp_client(endpoint, token) as c:
        out = await ok(c, body["id"])
    card = patched["recommendation"]["recommended"]
    assert out["recommended"]["sum_insured"] == option == card["sum_insured"]
    assert out["recommended"]["premium"] == card["premium"]


@pytest.mark.usefixtures("seed")
async def test_incomplete_without_concerns_has_no_side_effects(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    c = comparison(user)
    session.add(c)
    await session.commit()
    token = await make_token(session, user)
    before = c.model_dump()

    async with mcp_client(endpoint, token) as client:
        out = await ok(client, c.id)

    assert out["status"] == "incomplete"
    assert "在意事項" in out["missing"]
    assert out["recommended"] is None and out["alternative"] is None
    assert out["concerns"] == []
    assert out["trip"]["days"] == 5
    session.expire_all()
    after = (await session.exec(select(Comparison))).one()
    assert after.model_dump() == before  # 沒有算出推薦、沒有改動
    assert after.recommended_plan_code is None and after.updated_at == c.updated_at


@pytest.mark.usefixtures("seed")
async def test_incomplete_with_concerns_but_no_recommendation(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    c = comparison(user, concerns=["班機延誤"])
    session.add(c)
    await session.commit()
    token = await make_token(session, user)
    async with mcp_client(endpoint, token) as client:
        out = await ok(client, c.id)
    assert out["status"] == "incomplete"
    assert "推薦" in out["missing"]
    assert out["concerns"] == ["班機延誤"]
    session.expire_all()
    assert (await session.exec(select(Comparison))).one().recommended_plan_code is None


async def test_incomplete_when_saved_plan_no_longer_quoted(
    session: AsyncSession, endpoint: McpEndpoint
):
    """報價表沒有資料（或方案已移除）時不丟錯，回 incomplete。"""
    user = await make_user(session, "a@example.com")
    c = comparison(
        user,
        concerns=["班機延誤"],
        recommended_plan_code="fubon_advanced",
        recommended_sum_insured=6_000_000,
        alternative_plan_code="huanan_basic",
        alternative_sum_insured=3_000_000,
    )
    session.add(c)
    await session.commit()
    token = await make_token(session, user)
    async with mcp_client(endpoint, token) as client:  # 沒有 seed：沒有報價
        out = await ok(client, c.id)
    assert out["status"] == "incomplete" and "報價" in out["missing"]


async def test_not_found_and_not_owner_look_identical(
    session: AsyncSession, endpoint: McpEndpoint
):
    import uuid

    alice = await make_user(session, "a@example.com")
    bob = await make_user(session, "b@example.com")
    bobs = comparison(bob, "韓國", concerns=["班機延誤"])
    session.add(bobs)
    await session.commit()
    token = await make_token(session, alice)

    async with mcp_client(endpoint, token) as client:
        other = await get(client, bobs.id)
        missing = await get(client, uuid.uuid4())

    assert other.is_error and missing.is_error
    assert other.content == missing.content
    assert other.structured_content == missing.structured_content
    assert NOT_FOUND in str(other.content)
    assert "韓國" not in str(other)
    # 都記成失敗的呼叫，內容不入稽核
    logs = (await session.exec(select(McpCallLog))).all()
    assert [log.success for log in logs] == [False, False]


async def test_each_user_only_sees_own(session: AsyncSession, endpoint: McpEndpoint):
    alice = await make_user(session, "a@example.com")
    bob = await make_user(session, "b@example.com")
    mine, theirs = comparison(alice, "日本"), comparison(bob, "韓國")
    session.add_all([mine, theirs])
    await session.commit()
    async with mcp_client(endpoint, await make_token(session, alice)) as client:
        assert (await ok(client, mine.id))["trip"]["destination"] == "日本"
        assert (await get(client, theirs.id)).is_error
    async with mcp_client(endpoint, await make_token(session, bob)) as client:
        assert (await ok(client, theirs.id))["trip"]["destination"] == "韓國"
        assert (await get(client, mine.id)).is_error


@pytest.mark.usefixtures("seed")
async def test_output_fields_are_an_allowlist(
    session: AsyncSession, client: AsyncClient, endpoint: McpEndpoint
):
    user: User = await make_user(session, "secret-owner@example.com")
    app.dependency_overrides[current_user] = lambda: user
    try:
        created = (
            await client.post(
                "/comparisons", json=trip(6, ["班機延誤"], city="東京澀谷區")
            )
        ).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    row = await session.get(Comparison, created["id"])
    assert row is not None
    # 日程活動、推薦的在意事項理由都是自由文字
    session.add(
        ItineraryActivity(
            comparison_id=row.id,
            date=row.depart_date,
            hour=9,
            title="祕密活動標題",
            category="eat",
            duration=1.0,
        )
    )
    row.concern_suggestions = [{"concern": "班機延誤", "reason": "自由文字理由"}]
    session.add(row)
    await session.commit()
    token = await make_token(session, user)

    async with mcp_client(endpoint, token) as c:
        out = await ok(c, created["id"])
        raw = str(await get(c, created["id"]))

    assert set(out) == TOP_KEYS
    assert set(out["trip"]) == TRIP_KEYS
    assert set(out["recommended"]) == PLAN_KEYS
    assert set(out["alternative"]) == PLAN_KEYS
    for banned in (
        "東京澀谷區",
        "自由文字理由",
        "祕密活動標題",
        "secret-owner@",
        "http",
    ):
        assert banned not in raw
    assert "reason" not in json.dumps(out) and "basis" not in json.dumps(out)


async def test_incomplete_output_has_same_key_set(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    c = comparison(user, city="大阪")
    session.add(c)
    await session.commit()
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        out = await ok(client, c.id)
    assert set(out) == TOP_KEYS and set(out["trip"]) == TRIP_KEYS
    assert "大阪" not in json.dumps(out, ensure_ascii=False)


async def test_tool_is_listed_read_only(session: AsyncSession, endpoint: McpEndpoint):
    user = await make_user(session, "a@example.com")
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    tool = tools["get_comparison"]
    assert tool.annotations and tool.annotations.read_only_hint is True
    assert "找不到" in (tool.description or "")
    assert "get_comparison" in (tools["list_comparisons"].description or "")
