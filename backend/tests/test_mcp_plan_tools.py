"""MCP list_options 與 get_plan_details（#509）：與程式定義、網站 API 逐項相等；輸出 key 集合鎖住。"""

import pytest
from app.domain.benefits import Concern
from app.domain.destinations import Destination
from app.domain.plans import load_plans
from app.ingest.plan_matrix import read_plan_data
from app.mcp_server.endpoint import McpEndpoint
from app.mcp_server.tools.get_plan_details import (
    NO_SUM_INSURED_HINT,
    PLAN_NOT_FOUND,
    UPLOAD_ONLY,
)
from app.models import McpCallLog
from app.seed import load_plan_data
from httpx import AsyncClient
from mcp import Client
from mcp.types import CallToolResult
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import test_mcp_endpoint as base

# 重用 test_mcp_endpoint 的 MCP 端點 fixture
endpoint, clock, rate_limit = base.endpoint, base.clock, base.rate_limit
make_user, make_token, mcp_client = base.make_user, base.make_token, base.mcp_client

OPTIONS_KEYS = {"destinations", "concerns", "max_concerns"}
DETAILS_KEYS = {
    "plan_id",
    "name",
    "company",
    "sum_insured_options",
    "sum_insured",
    "benefits",
    "premiums",
    "note",
}
BENEFIT_KEYS = {"name", "category", "amount"}
PREMIUM_KEYS = {"days", "premium"}


@pytest.fixture
async def seed(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())


@pytest.fixture
async def token(session: AsyncSession) -> str:
    return await make_token(session, await make_user(session, "a@example.com"))


async def details(client: Client, **args: object) -> CallToolResult:
    return await client.call_tool("get_plan_details", args)


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]


# ---- list_options ----


async def test_list_options_matches_source_enums(endpoint: McpEndpoint, token: str):
    async with mcp_client(endpoint, token) as c:
        result = await c.call_tool("list_options", {})
    assert not result.is_error and result.structured_content is not None
    out = result.structured_content
    assert set(out) == OPTIONS_KEYS
    assert out["destinations"] == [d.value for d in Destination]
    assert out["concerns"] == [c.value for c in Concern]
    assert len(out["destinations"]) == 16 and len(out["concerns"]) == 9
    assert out["max_concerns"] == 3


async def test_list_options_needs_no_data_and_is_audited(
    session: AsyncSession, endpoint: McpEndpoint, token: str
):
    async with mcp_client(endpoint, token) as c:
        await c.call_tool("list_options", {})
    logs = (await session.exec(select(McpCallLog))).all()
    assert [(log.tool_name, log.success) for log in logs] == [("list_options", True)]


# ---- get_plan_details ----


@pytest.mark.usefixtures("seed")
async def test_matches_web_benefits_and_sum_insured_options(
    client: AsyncClient, endpoint: McpEndpoint, token: str
):
    plans = [p for p in load_plans() if not p.upload_only]
    assert plans
    async with mcp_client(endpoint, token) as c:
        for plan in plans:
            web_options = (
                await client.get(f"/plans/{plan.id}/sum-insured-options")
            ).json()["sum_insured"]
            assert web_options
            bare = await details(c, plan_id=plan.id)
            assert not bare.is_error and bare.structured_content is not None
            assert bare.structured_content["sum_insured_options"] == web_options
            assert bare.structured_content["benefits"] == []
            assert bare.structured_content["note"] == NO_SUM_INSURED_HINT
            for amount in web_options:
                web = (
                    await client.get(
                        f"/plans/{plan.id}/benefits", params={"sum_insured": amount}
                    )
                ).json()
                out = await details(c, plan_id=plan.id, sum_insured=amount)
                assert not out.is_error and out.structured_content is not None
                got = out.structured_content
                assert (
                    got["benefits"] == web["benefits"]
                )  # 逐項相等：名稱、類別、額度、順序
                assert got["sum_insured"] == web["sum_insured"] == amount
                assert (got["plan_id"], got["name"], got["company"]) == (
                    plan.id,
                    plan.name,
                    plan.company,
                )
                assert got["note"] is None


@pytest.mark.usefixtures("seed")
async def test_premiums_match_web_plan_premiums(
    client: AsyncClient, endpoint: McpEndpoint, token: str
):
    plan = next(p for p in load_plans() if not p.upload_only)
    options = (await client.get(f"/plans/{plan.id}/sum-insured-options")).json()[
        "sum_insured"
    ]
    amount = options[0]
    async with mcp_client(endpoint, token) as c:
        out = (await details(c, plan_id=plan.id, sum_insured=amount)).structured_content
    assert out is not None and out["premiums"]
    days = [q["days"] for q in out["premiums"]]
    assert days == sorted(days)
    web_days = (await client.get("/plan-premiums/options")).json()["days"]
    for q in out["premiums"]:
        assert q["days"] in web_days
        web = (
            await client.get(
                "/plan-premiums", params={"sum_insured": amount, "days": q["days"]}
            )
        ).json()
        assert [w["premium"] for w in web if w["plan_code"] == plan.id] == [
            q["premium"]
        ]
    # 網站沒有報價的天數，工具也不會有（不推估）
    for d in set(web_days) - set(days):
        web = (
            await client.get(
                "/plan-premiums", params={"sum_insured": amount, "days": d}
            )
        ).json()
        assert plan.id not in [w["plan_code"] for w in web]


@pytest.mark.usefixtures("seed")
async def test_output_key_sets_are_locked(
    client: AsyncClient, endpoint: McpEndpoint, token: str
):
    plan = next(p for p in load_plans() if not p.upload_only)
    amount = (await client.get(f"/plans/{plan.id}/sum-insured-options")).json()[
        "sum_insured"
    ][0]
    async with mcp_client(endpoint, token) as c:
        out = (await details(c, plan_id=plan.id, sum_insured=amount)).structured_content
    assert out is not None and set(out) == DETAILS_KEYS
    assert all(set(b) == BENEFIT_KEYS for b in out["benefits"])
    assert all(set(q) == PREMIUM_KEYS for q in out["premiums"])


@pytest.mark.usefixtures("seed")
async def test_unknown_and_upload_only_plans_have_distinct_clear_errors(
    session: AsyncSession, endpoint: McpEndpoint, token: str
):
    upload_only = next((p for p in load_plans() if p.upload_only), None)
    assert upload_only is not None
    async with mcp_client(endpoint, token) as c:
        missing = await details(c, plan_id="no-such-plan", sum_insured=1)
        only = await details(c, plan_id=upload_only.id)
    assert error_text(missing).endswith(PLAN_NOT_FOUND)
    assert error_text(only).endswith(UPLOAD_ONLY)
    # 訊息是固定文字：不回傳方案名稱或其他資訊
    assert upload_only.name not in error_text(only)
    assert "no-such-plan" not in error_text(missing)
    logs = (await session.exec(select(McpCallLog))).all()
    assert [log.success for log in logs] == [False, False]


@pytest.mark.usefixtures("seed")
async def test_sum_insured_not_offered_lists_options(
    client: AsyncClient, endpoint: McpEndpoint, token: str
):
    plan = next(p for p in load_plans() if not p.upload_only)
    options = (await client.get(f"/plans/{plan.id}/sum-insured-options")).json()[
        "sum_insured"
    ]
    async with mcp_client(endpoint, token) as c:
        result = await details(c, plan_id=plan.id, sum_insured=12345)
    text = error_text(result)
    assert "12345" in text and all(str(o) in text for o in options)


@pytest.mark.usefixtures("seed")
async def test_readonly_token_can_call_both_tools(endpoint: McpEndpoint, token: str):
    async with mcp_client(endpoint, token) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    for name in ("list_options", "get_plan_details"):
        tool = tools[name]
        assert tool.annotations is not None and tool.annotations.read_only_hint
        assert tool.description is not None
        # 說明是繁體中文
        assert any("\u4e00" <= ch <= "\u9fff" for ch in tool.description)
