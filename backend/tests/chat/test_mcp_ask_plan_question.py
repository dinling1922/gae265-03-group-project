"""MCP ask_plan_question（#511）：與網站 1-4 方案問答同一套邏輯，唯讀範圍、LLM 工具限流、不存檔。

LLM 與 Jev guard 用假實作注入（沿用 test_questions_api 的 FakeModel／FakeGuard）。
"""

import pytest
from app.api.deps import current_user
from app.api.routes.conversations import answer_model
from app.api.routes.questions import (
    MAX_HISTORY_TURNS,
    MAX_QUESTION_LENGTH,
    question_guard,
)
from app.domain.chat import ORIGINAL_ONLY_NOTICE
from app.domain.questions import NOT_COVERED, OUT_OF_SCOPE
from app.ingest.plan_matrix import read_plan_data
from app.main import app
from app.mcp_server import registry
from app.mcp_server.endpoint import McpEndpoint
from app.mcp_server.tools import ask_plan_question as tool
from app.mcp_server.tools.get_comparison import NOT_FOUND
from app.models import Comparison, Conversation, McpCallLog, Message, User
from app.seed import load_plan_data
from httpx import AsyncClient
from langchain_core.runnables import RunnableLambda
from mcp import Client
from mcp.types import CallToolResult
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import test_mcp_endpoint as base
from tests.chat.test_questions_api import (
    ASK,
    NO_MATCH,
    TRIP,
    FakeGuard,
    FakeModel,
)

endpoint, clock, rate_limit = base.endpoint, base.clock, base.rate_limit
mcp_client = base.mcp_client

pytestmark = pytest.mark.usefixtures("user", "plan_data")

TOP_KEYS = {"answer", "original_only", "citations"}
PLAN_KEYS = {"plan_code", "plan_name", "recommended", "citations"}
CITATION_KEYS = {
    "insurance_code",
    "insurance_name",
    "article_no",
    "title",
    "section",
    "page_start",
    "page_end",
    "excerpt",
}


def use(
    monkeypatch: pytest.MonkeyPatch,
    model: FakeModel | None = None,
    guard: FakeGuard | None = None,
) -> None:
    """同一組假 LLM 與 guard 同時給網站（dependency_overrides）與 MCP 工具。"""
    model = model or FakeModel()
    guard = guard or FakeGuard()
    app.dependency_overrides[answer_model] = lambda: RunnableLambda(model)
    app.dependency_overrides[question_guard] = lambda: RunnableLambda(guard)
    monkeypatch.setattr(tool, "answer_model", lambda: RunnableLambda(model))
    monkeypatch.setattr(tool, "question_guard", lambda: RunnableLambda(guard))


@pytest.fixture(autouse=True)
def cleanup_overrides(monkeypatch: pytest.MonkeyPatch):
    use(monkeypatch)
    yield
    app.dependency_overrides.pop(answer_model, None)
    app.dependency_overrides.pop(question_guard, None)


@pytest.fixture
async def plan_data(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())


@pytest.fixture
async def comparison_id(client: AsyncClient) -> str:
    r = await client.post("/comparisons", json=TRIP)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
async def token(session: AsyncSession, user: User) -> str:
    return await base.make_token(session, user)


async def ask(
    client: Client, comparison_id: object, question: str = ASK, **extra: object
) -> CallToolResult:
    return await client.call_tool(
        "ask_plan_question",
        {"comparison_id": str(comparison_id), "question": question} | extra,
    )


async def ok(client: Client, comparison_id: object, question: str = ASK, **extra):
    result = await ask(client, comparison_id, question, **extra)
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]


def web_view(body: dict) -> dict:
    """網站回應去掉 MCP 不給的欄位（rule_id、score）。"""
    return {
        **body,
        "citations": [
            {
                **g,
                "citations": [
                    {k: v for k, v in c.items() if k in CITATION_KEYS}
                    for c in g["citations"]
                ],
            }
            for g in body["citations"]
        ],
    }


# ---- 與網站一致 ----


@pytest.mark.usefixtures("catalog_loaded")
async def test_same_answer_and_citations_as_web(
    client: AsyncClient,
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    answer = "兩個方案都可能適用，申請方式請看下方條文。"
    use(monkeypatch, FakeModel(answer, answer))
    web = (
        await client.post(
            f"/comparisons/{comparison_id}/questions", json={"question": ASK}
        )
    ).json()
    async with mcp_client(endpoint, token) as c:
        out = await ok(c, comparison_id)
    assert set(out) == TOP_KEYS
    assert out == web_view(web)
    assert out["answer"] == answer and out["original_only"] is False
    assert [(g["plan_code"], g["recommended"]) for g in out["citations"]] == [
        ("fubon_advanced", True),
        ("huanan_basic", False),
    ]
    for group in out["citations"]:
        assert set(group) == PLAN_KEYS and group["citations"]
        assert all(set(c) == CITATION_KEYS for c in group["citations"])


@pytest.mark.usefixtures("catalog_loaded")
async def test_history_is_used_to_rewrite_like_web(
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    model = FakeModel(ASK, "次推薦要檢附收據申請。")
    guard = FakeGuard()
    use(monkeypatch, model, guard)
    async with mcp_client(endpoint, token) as c:
        out = await ok(
            c,
            comparison_id,
            "那次推薦呢？",
            history=[{"question": ASK, "answer": "推薦方案要檢附收據申請。"}],
        )
    assert out["answer"] == "次推薦要檢附收據申請。"
    assert guard.requests[0]["state"]["question"] == ASK  # 改寫後才送 guard
    assert any("推薦方案要檢附收據申請。" in str(m.content) for m in model.calls[0])


@pytest.mark.usefixtures("catalog_loaded")
async def test_unsupported_number_lists_original_text_only(
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    use(monkeypatch, FakeModel("最高可給付 9,876,543 元。"))
    async with mcp_client(endpoint, token) as c:
        out = await ok(c, comparison_id)
    assert out["original_only"] is True
    assert out["answer"].startswith(ORIGINAL_ONLY_NOTICE)
    assert "9876543" not in out["answer"].replace(",", "")
    assert out["citations"]


@pytest.mark.usefixtures("catalog_loaded")
async def test_llm_failure_falls_back_to_original_text(
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    def boom(_):
        raise RuntimeError("provider down")

    monkeypatch.setattr(tool, "answer_model", lambda: RunnableLambda(boom))
    async with mcp_client(endpoint, token) as c:
        out = await ok(c, comparison_id)
    assert out["original_only"] is True and out["citations"]


@pytest.mark.parametrize(("related", "injection"), [(0.1, 0.1), (0.9, 0.9)])
async def test_guard_blocks_like_web(
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    related: float,
    injection: float,
):
    model = FakeModel()
    use(monkeypatch, model, FakeGuard(related, injection))
    async with mcp_client(endpoint, token) as c:
        out = await ok(c, comparison_id, "今天天氣如何？")
    assert out == {"answer": OUT_OF_SCOPE, "original_only": False, "citations": []}
    assert model.calls == []


@pytest.mark.usefixtures("catalog_loaded")
async def test_not_covered(comparison_id: str, endpoint: McpEndpoint, token: str):
    # 檢索不到條文且 LLM 失敗（預設的 FakeModel 沒有回覆）
    async with mcp_client(endpoint, token) as c:
        out = await ok(c, comparison_id, NO_MATCH)
    assert out == {"answer": NOT_COVERED, "original_only": False, "citations": []}


@pytest.mark.usefixtures("catalog_loaded")
async def test_nothing_is_stored(
    session: AsyncSession,
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    use(monkeypatch, FakeModel("請看下方條文。"))
    before = await session.get(Comparison, comparison_id)
    assert before is not None
    updated_at = before.updated_at
    async with mcp_client(endpoint, token) as c:
        await ok(c, comparison_id)
    for table in (Conversation, Message):
        assert (await session.exec(select(func.count()).select_from(table))).one() == 0
    session.expire_all()
    after = await session.get(Comparison, comparison_id)
    assert after is not None and after.updated_at == updated_at


# ---- 輸入限制 ----


@pytest.mark.usefixtures("catalog_loaded")
async def test_question_length_and_history_bounds(
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    assert (MAX_QUESTION_LENGTH, MAX_HISTORY_TURNS) == (300, 3)
    turn = {"question": "q", "answer": "a"}
    use(monkeypatch, FakeModel(ASK, "請看下方條文。"))
    async with mcp_client(endpoint, token) as c:
        for bad_question in ("字" * 301, "   ", ""):
            assert (await ask(c, comparison_id, bad_question)).is_error
        assert (await ask(c, comparison_id, history=[turn] * 4)).is_error
        # 邊界：300 字、3 輪都可以
        result = await ask(c, comparison_id, "字" * 300, history=[turn] * 3)
        assert not result.is_error, result


# ---- 找不到、隔離 ----


async def test_other_users_and_missing_comparison_get_identical_response(
    session: AsyncSession,
    comparison_id: str,
    endpoint: McpEndpoint,
    other_user: User,
):
    other_token = await base.make_token(session, other_user)
    missing = "00000000-0000-4000-8000-000000000000"
    async with mcp_client(endpoint, other_token) as c:
        not_mine = await ask(c, comparison_id)
        absent = await ask(c, missing)
    assert error_text(not_mine) == error_text(absent)
    assert NOT_FOUND in error_text(absent)
    assert not_mine.content == absent.content
    assert not_mine.structured_content == absent.structured_content


async def test_without_recommendation_is_an_error(
    client: AsyncClient, endpoint: McpEndpoint, token: str
):
    r = await client.post("/comparisons", json=TRIP | {"concerns": []})
    async with mcp_client(endpoint, token) as c:
        text = error_text(await ask(c, r.json()["id"]))
    assert "還沒有推薦方案" in text


async def test_llm_unavailable_is_a_clear_error(
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    def broken():
        raise RuntimeError("secret-endpoint")

    monkeypatch.setattr(tool, "answer_model", broken)
    async with mcp_client(endpoint, token) as c:
        text = error_text(await ask(c, comparison_id))
    assert tool.UNAVAILABLE in text and "secret-endpoint" not in text


# ---- 範圍、限流、稽核 ----


@pytest.mark.usefixtures("catalog_loaded")
@pytest.mark.parametrize("scopes", [("read",), ("read", "write")])
async def test_read_scope_is_enough(
    session: AsyncSession, comparison_id: str, endpoint: McpEndpoint, scopes: tuple
):
    user = (await session.exec(select(User))).one()
    token = await _token_for(session, user, scopes)
    async with mcp_client(endpoint, token) as c:
        assert not (await ask(c, comparison_id)).is_error


async def _token_for(session: AsyncSession, user: User, scopes: tuple) -> str:
    from app.domain.access_tokens import create_token

    _, plaintext = await create_token(session, user.id, "測試", scopes)
    return plaintext


async def test_llm_tool_rate_limit_and_audit(
    session: AsyncSession,
    comparison_id: str,
    endpoint: McpEndpoint,
    token: str,
    clock: base.Clock,
    monkeypatch: pytest.MonkeyPatch,
):
    model = FakeModel()
    use(monkeypatch, model, FakeGuard(related=0.0))  # 一律擋下，不用檢索，也不呼叫 LLM
    async with mcp_client(endpoint, token) as c:
        for _ in range(10):
            assert not (await ask(c, comparison_id)).is_error
        assert "每分鐘最多 10 次" in error_text(await ask(c, comparison_id))
        clock.now += 61
        assert not (await ask(c, comparison_id)).is_error
        # 找不到也記為失敗
        assert (await ask(c, "00000000-0000-4000-8000-000000000000")).is_error
    rows = (await session.exec(select(McpCallLog))).all()
    assert (
        sorted(r.success for r in rows if r.tool_name == "ask_plan_question")
        == [
            False,
            False,
        ]
        + [True] * 11
    )


def test_registered_as_read_scope_llm_tool():
    registry.load_tool_modules()
    spec = registry.SPECS["ask_plan_question"]
    assert (
        spec.scope == "read" and spec.calls_llm and spec.rate_limit_per_minute is None
    )


async def test_other_user_cannot_see_via_web_override_leftovers(
    client: AsyncClient, comparison_id: str, other_user: User
):
    """對照組：網站同樣把別人的紀錄當不存在（404），MCP 的行為與它一致。"""
    app.dependency_overrides[current_user] = lambda: other_user
    r = await client.post(
        f"/comparisons/{comparison_id}/questions", json={"question": ASK}
    )
    assert r.status_code == 404
