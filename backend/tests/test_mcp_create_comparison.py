"""MCP create_comparison（#510）：LLM 只抽取、程式驗證，資訊不足不寫入，推薦與網頁一致。

LLM 用假的抽取函式注入、「今天」固定為 2026-11-19（週四，台北時間）。
"""

from datetime import date

import pytest
from app.api.deps import current_user
from app.domain import trip_extraction
from app.domain.trip_extraction import Fixed
from app.ingest.plan_matrix import read_plan_data
from app.main import app
from app.mcp_server.endpoint import McpEndpoint
from app.mcp_server.registry import WRITE_SCOPE_REQUIRED
from app.models import Comparison, McpCallLog, User
from app.seed import load_plan_data
from httpx import AsyncClient
from mcp import Client
from mcp.types import CallToolResult
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import test_mcp_endpoint as base
from tests.test_mcp_scopes import make_token as make_scoped_token

endpoint, clock, rate_limit = base.endpoint, base.clock, base.rate_limit
make_user, mcp_client = base.make_user, base.mcp_client

TODAY = date(2026, 11, 19)
TOP_KEYS = {"status", "comparison", "understood", "missing", "unsupported"}
UNDERSTOOD_KEYS = {"destination", "depart_date", "return_date", "days", "concerns"}
MISSING_KEYS = {"field", "message", "options"}
COMPARISON_KEYS = {
    "id",
    "status",
    "missing",
    "trip",
    "concerns",
    "recommended",
    "alternative",
}

GOOD = {
    "destination": "日本",
    "depart_date": "2026-12-20",
    "return_date": "2026-12-25",
    "concerns": ["班機延誤", "突然生病就醫"],
}


class FakeExtractor:
    def __init__(self, output: object = None) -> None:
        self.output = GOOD if output is None else output
        self.calls: list[tuple[str, date, Fixed]] = []

    async def __call__(self, request: str, today: date, fixed: Fixed) -> object:
        self.calls.append((request, today, fixed))
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeExtractor:
    fake = FakeExtractor()
    monkeypatch.setattr(trip_extraction, "get_trip_extractor", lambda: fake)
    monkeypatch.setattr(trip_extraction, "today_taipei", lambda: TODAY)
    return fake


@pytest.fixture
async def seed(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())


@pytest.fixture
async def write_token(session: AsyncSession) -> str:
    return await make_scoped_token(session, "a@example.com", ("read", "write"))


async def count(session: AsyncSession) -> int:
    session.expire_all()
    return (await session.exec(select(func.count()).select_from(Comparison))).one()


async def create(client: Client, request: str = "12/20 去日本", **extra: object):
    return await client.call_tool("create_comparison", {"request": request} | extra)


async def ok(client: Client, request: str = "12/20 去日本", **extra: object) -> dict:
    result = await create(client, request, **extra)
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]


# ---- 成功 ----


@pytest.mark.usefixtures("seed", "llm")
async def test_created_matches_web_api(
    session: AsyncSession,
    client: AsyncClient,
    endpoint: McpEndpoint,
    write_token: str,
):
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c, "12/20 到 12/25 去東京，怕班機延誤跟生病")
    assert set(out) == TOP_KEYS and out["status"] == "created"
    assert out["missing"] == [] and out["unsupported"] == []
    assert out["understood"] == {
        "destination": "日本",
        "depart_date": "2026-12-20",
        "return_date": "2026-12-25",
        "days": 6,
        "concerns": ["班機延誤", "突然生病就醫"],
    }
    assert set(out["understood"]) == UNDERSTOOD_KEYS
    comparison = out["comparison"]
    assert set(comparison) == COMPARISON_KEYS and comparison["status"] == "complete"

    # 比較紀錄屬於呼叫者，存了推薦與在意事項
    row = (await session.exec(select(Comparison))).one()
    user = (await session.exec(select(User))).one()
    assert str(row.id) == comparison["id"] and row.user_id == user.id
    assert row.concerns == ["班機延誤", "突然生病就醫"]
    assert row.recommended_plan_code and row.alternative_plan_code

    # 與網站同一筆資料的 GET /comparisons/{id} 一致
    app.dependency_overrides[current_user] = lambda: user
    try:
        web = (await client.get(f"/comparisons/{comparison['id']}")).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    for key in ("recommended", "alternative"):
        card = web["recommendation"][key]
        assert comparison[key] == {
            "plan_id": card["plan_code"],
            "name": card["name"],
            "company": card["company"],
            "sum_insured": card["sum_insured"],
            "premium": card["premium"],
        }
    assert comparison["trip"]["days"] == 6


@pytest.mark.usefixtures("seed")
async def test_same_recommendation_as_web_create(
    session: AsyncSession,
    client: AsyncClient,
    endpoint: McpEndpoint,
    write_token: str,
    llm: FakeExtractor,
):
    """同樣的旅行規劃與在意事項，MCP 與網站 POST /comparisons 存下一樣的推薦與保額。"""
    user = (await session.exec(select(User))).one()
    app.dependency_overrides[current_user] = lambda: user
    try:
        web = (
            await client.post(
                "/comparisons",
                json={
                    "destination": "日本",
                    "depart_date": "2026-12-20",
                    "return_date": "2026-12-25",
                    "concerns": ["班機延誤", "突然生病就醫"],
                },
            )
        ).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c)
    assert out["comparison"]["id"] != web["id"]
    for key in ("recommended", "alternative"):
        card = web["recommendation"][key]
        got = out["comparison"][key]
        assert (got["name"], got["sum_insured"], got["premium"]) == (
            card["name"],
            card["sum_insured"],
            card["premium"],
        )
    assert llm.calls and llm.calls[0][1] == TODAY  # 「今天」有交給抽取器


@pytest.mark.usefixtures("seed")
@pytest.mark.parametrize(
    ("output", "depart", "ret"),
    [
        # 「5 天」含首尾：回程＝出發＋4
        ({**GOOD, "return_date": None, "stated_days": 5}, "2026-12-20", "2026-12-24"),
        # 「4 天 3 夜」LLM 抄 4 天；「5 晚」是 6 天
        ({**GOOD, "return_date": None, "stated_days": 4}, "2026-12-20", "2026-12-23"),
        ({**GOOD, "return_date": None, "stated_nights": 5}, "2026-12-20", "2026-12-25"),
        # 日期與天數一致時照用
        ({**GOOD, "stated_days": 6}, "2026-12-20", "2026-12-25"),
        # 邊界：今天出發、剛好 4 與 10 天
        (
            {
                **GOOD,
                "depart_date": "2026-11-19",
                "return_date": None,
                "stated_days": 4,
            },
            "2026-11-19",
            "2026-11-22",
        ),
        (
            {**GOOD, "return_date": None, "stated_days": 10},
            "2026-12-20",
            "2026-12-29",
        ),
        # 出發日在一年內的最後一天
        (
            {**GOOD, "depart_date": "2027-11-19", "return_date": "2027-11-24"},
            "2027-11-19",
            "2027-11-24",
        ),
    ],
)
async def test_dates_are_computed_by_program(
    endpoint: McpEndpoint,
    write_token: str,
    llm: FakeExtractor,
    output: dict,
    depart: str,
    ret: str,
):
    llm.output = output
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c)
    assert out["status"] == "created", out
    assert (out["understood"]["depart_date"], out["understood"]["return_date"]) == (
        depart,
        ret,
    )


@pytest.mark.usefixtures("seed")
async def test_unsupported_requests_are_listed(
    endpoint: McpEndpoint, write_token: str, llm: FakeExtractor
):
    llm.output = {**GOOD, "unsupported": ["最便宜", "70 歲", "最便宜", "預算 3000"]}
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c)
    assert out["status"] == "created"
    assert [u["request"] for u in out["unsupported"]] == [
        "最便宜",
        "70 歲",
        "預算 3000",
    ]
    assert all("不納入推薦" in u["note"] for u in out["unsupported"])


@pytest.mark.usefixtures("seed")
async def test_overrides_skip_llm_for_those_fields(
    session: AsyncSession, endpoint: McpEndpoint, write_token: str, llm: FakeExtractor
):
    """overrides 有值就直接用，LLM 的同欄位結果被忽略，也告訴 LLM 不必抽取。"""
    llm.output = {"destination": "韓國", "concerns": ["證件遺失"], "stated_days": 5}
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(
            c,
            "去玩",
            overrides={
                "destination": "日本",
                "depart_date": "2026-12-20",
                "return_date": "2026-12-25",
                "concerns": ["行李延誤", "信用卡盜刷"],
            },
        )
    assert out["status"] == "created"
    assert out["understood"]["destination"] == "日本"
    assert out["understood"]["concerns"] == ["行李延誤", "信用卡盜刷"]
    assert out["understood"]["days"] == 6  # 明確的回程日優先，LLM 抄的 5 天不用
    assert llm.calls[0][2] == Fixed(
        "日本", date(2026, 12, 20), date(2026, 12, 25), ["行李延誤", "信用卡盜刷"]
    )
    row = (await session.exec(select(Comparison))).one()
    assert row.destination == "日本" and row.concerns == ["行李延誤", "信用卡盜刷"]


@pytest.mark.usefixtures("seed")
async def test_overrides_fill_only_what_llm_misses(
    endpoint: McpEndpoint, write_token: str, llm: FakeExtractor
):
    llm.output = {"destination": None, "concerns": ["班機延誤"], "stated_days": 5}
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(
            c,
            "5 天，擔心班機延誤",
            overrides={"destination": "泰國", "depart_date": "2026-12-01"},
        )
    assert out["status"] == "created"
    assert out["understood"]["destination"] == "泰國"
    assert out["understood"]["return_date"] == "2026-12-05"


@pytest.mark.parametrize(
    "overrides",
    [
        {"destination": "埃及"},
        {"concerns": ["班機延誤", "班機延誤"]},
        {"concerns": ["班機延誤", "行李延誤", "證件遺失", "信用卡盜刷"]},
        {"depart_date": "不是日期"},
    ],
)
@pytest.mark.usefixtures("llm")
async def test_invalid_overrides_are_rejected_before_anything_runs(
    session: AsyncSession, endpoint: McpEndpoint, write_token: str, overrides: dict
):
    async with mcp_client(endpoint, write_token) as c:
        result = await create(c, overrides=overrides)
    assert result.is_error
    assert await count(session) == 0


# ---- 資訊不足：不寫入 ----

MISSING_CASES = [
    (
        "沒日期",
        {**GOOD, "depart_date": None, "return_date": None},
        {"depart_date", "trip_length"},
    ),
    ("只缺天數", {**GOOD, "return_date": None}, {"trip_length"}),
    ("目的地不在選項", {**GOOD, "destination": "埃及"}, {"destination"}),
    ("沒說目的地", {**GOOD, "destination": None}, {"destination"}),
    ("沒有在意事項", {**GOOD, "concerns": []}, {"concerns"}),
    (
        "超過 3 個在意事項",
        {**GOOD, "concerns": ["班機延誤", "行李延誤", "證件遺失", "信用卡盜刷"]},
        {"concerns"},
    ),
    ("在意事項重複", {**GOOD, "concerns": ["班機延誤", "班機延誤"]}, {"concerns"}),
    ("在意事項不在選項", {**GOOD, "concerns": ["天氣太熱"]}, {"concerns"}),
    (
        "過去的日期",
        {**GOOD, "depart_date": "2026-11-18", "return_date": "2026-11-23"},
        {"depart_date"},
    ),
    (
        "超過一年",
        {**GOOD, "depart_date": "2027-11-20", "return_date": "2027-11-25"},
        {"depart_date"},
    ),
    ("日期格式錯", {**GOOD, "depart_date": "12/20"}, {"depart_date"}),
    ("回程早於出發", {**GOOD, "return_date": "2026-12-19"}, {"trip_length"}),
    ("只有 3 天", {**GOOD, "return_date": "2026-12-22"}, {"trip_length"}),
    ("超過 10 天", {**GOOD, "return_date": None, "stated_days": 11}, {"trip_length"}),
    ("日期與天數對不起來", {**GOOD, "stated_days": 9}, {"trip_length"}),
    (
        "全都缺",
        {"destination": None, "depart_date": None, "concerns": []},
        {"destination", "depart_date", "trip_length", "concerns"},
    ),
]


@pytest.mark.usefixtures("seed")
@pytest.mark.parametrize(
    ("output", "fields"),
    [pytest.param(o, f, id=n) for n, o, f in MISSING_CASES],
)
async def test_needs_more_info_writes_nothing(
    session: AsyncSession,
    endpoint: McpEndpoint,
    write_token: str,
    llm: FakeExtractor,
    output: dict,
    fields: set[str],
):
    llm.output = output
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c, "12/20 隨便說說")
    assert set(out) == TOP_KEYS
    assert out["status"] == "needs_more_info" and out["comparison"] is None
    assert {m["field"] for m in out["missing"]} == fields
    assert all(set(m) == MISSING_KEYS and m["message"] for m in out["missing"])
    assert await count(session) == 0


@pytest.mark.usefixtures("seed")
async def test_needs_more_info_reports_what_was_understood_and_options(
    endpoint: McpEndpoint, write_token: str, llm: FakeExtractor
):
    llm.output = {
        **GOOD,
        "destination": "埃及",
        "return_date": None,
        "unsupported": ["最便宜"],
    }
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c)
    assert out["understood"] == {
        "destination": None,
        "depart_date": "2026-12-20",
        "return_date": None,
        "days": None,
        "concerns": ["班機延誤", "突然生病就醫"],
    }
    by_field = {m["field"]: m for m in out["missing"]}
    assert "埃及" in by_field["destination"]["message"]
    assert len(by_field["destination"]["options"]) == 16
    assert "日本" in by_field["destination"]["options"]
    assert [u["request"] for u in out["unsupported"]] == ["最便宜"]
    # 順序固定：目的地、出發日、天數、在意事項
    assert [m["field"] for m in out["missing"]] == ["destination", "trip_length"]


@pytest.mark.usefixtures("seed")
async def test_concern_options_are_listed_when_missing(
    endpoint: McpEndpoint, write_token: str, llm: FakeExtractor
):
    llm.output = {**GOOD, "concerns": []}
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c)
    (missing,) = out["missing"]
    assert len(missing["options"]) == 9 and "證件遺失" in missing["options"]


# ---- LLM 失敗 ----


@pytest.mark.usefixtures("seed")
@pytest.mark.parametrize(
    "output",
    [
        RuntimeError("連線失敗 secret-endpoint"),
        TimeoutError(),
        {"destination": ["不是字串"]},
        "不是物件",
    ],
)
async def test_llm_failure_is_a_clear_error_and_writes_nothing(
    session: AsyncSession,
    endpoint: McpEndpoint,
    write_token: str,
    llm: FakeExtractor,
    output: object,
):
    llm.output = output
    async with mcp_client(endpoint, write_token) as c:
        result = await create(c)
    text = error_text(result)
    assert "沒有建立任何資料" in text and "secret-endpoint" not in text
    assert await count(session) == 0
    logs = (await session.exec(select(McpCallLog))).all()
    assert [(log.tool_name, log.success) for log in logs] == [
        ("create_comparison", False)
    ]


@pytest.mark.usefixtures("seed", "llm")
async def test_llm_timeout_is_enforced(
    endpoint: McpEndpoint,
    write_token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    async def slow(*_: object) -> object:
        await asyncio.sleep(5)

    monkeypatch.setattr(trip_extraction, "get_trip_extractor", lambda: slow)
    monkeypatch.setattr(trip_extraction, "LLM_TIMEOUT_SECONDS", 0.05)
    async with mcp_client(endpoint, write_token) as c:
        result = await create(c)
    assert "沒有建立任何資料" in error_text(result)


async def test_no_quotes_for_the_days_is_an_error_and_writes_nothing(
    session: AsyncSession, endpoint: McpEndpoint, write_token: str, llm: FakeExtractor
):
    """沒有 seed（沒有報價）：_recommend 的 409 轉成 ToolError，不留下半成品。"""
    assert llm
    async with mcp_client(endpoint, write_token) as c:
        result = await create(c)
    assert "無法產生推薦" in error_text(result)
    assert await count(session) == 0


# ---- 輸入限制 ----


@pytest.mark.usefixtures("llm")
@pytest.mark.parametrize("request_text", ["", "   ", "字" * 501])
async def test_request_length_bounds(
    endpoint: McpEndpoint, write_token: str, request_text: str
):
    async with mcp_client(endpoint, write_token) as c:
        assert (await create(c, request_text)).is_error


@pytest.mark.usefixtures("seed", "llm")
async def test_request_at_max_length_is_accepted(
    endpoint: McpEndpoint, write_token: str
):
    async with mcp_client(endpoint, write_token) as c:
        assert not (await create(c, "字" * 500)).is_error


# ---- 範圍、限流、稽核、隔離 ----


@pytest.mark.usefixtures("seed")
async def test_read_token_is_refused_and_nothing_runs(
    session: AsyncSession, endpoint: McpEndpoint, llm: FakeExtractor
):
    token = await make_scoped_token(session, "r@example.com", ("read",))
    async with mcp_client(endpoint, token) as c:
        result = await create(c)
    assert WRITE_SCOPE_REQUIRED in error_text(result)
    assert llm.calls == [] and await count(session) == 0
    logs = (await session.exec(select(McpCallLog))).all()
    assert [(log.tool_name, log.success) for log in logs] == [
        ("create_comparison", False)
    ]


@pytest.mark.usefixtures("seed", "llm")
async def test_audit_records_success(
    session: AsyncSession, endpoint: McpEndpoint, write_token: str
):
    async with mcp_client(endpoint, write_token) as c:
        await ok(c)
    logs = (await session.exec(select(McpCallLog))).all()
    assert [(log.tool_name, log.success) for log in logs] == [
        ("create_comparison", True)
    ]


@pytest.mark.usefixtures("llm")
async def test_llm_tool_rate_limit_is_10_per_minute(
    session: AsyncSession,
    endpoint: McpEndpoint,
    write_token: str,
    llm: FakeExtractor,
    clock: base.Clock,
):
    llm.output = {}  # 都缺：needs_more_info，不寫入，只測限流
    async with mcp_client(endpoint, write_token) as c:
        for _ in range(10):
            assert not (await create(c)).is_error
        blocked = await create(c)
        assert "每分鐘最多 10 次" in error_text(blocked)
        assert len(llm.calls) == 10  # 被擋的沒呼叫 LLM
        clock.now += 61
        assert not (await create(c)).is_error
    logs = (await session.exec(select(McpCallLog))).all()
    assert sorted(log.success for log in logs) == [False] + [True] * 11


@pytest.mark.usefixtures("seed", "llm")
async def test_comparison_belongs_to_caller_and_is_isolated(
    session: AsyncSession, endpoint: McpEndpoint, write_token: str
):
    other_token = await make_scoped_token(session, "b@example.com", ("read", "write"))
    async with mcp_client(endpoint, write_token) as a:
        created = await ok(a)
    async with mcp_client(endpoint, other_token) as b:
        # 別人看不到、也讀不到
        listed = await b.call_tool("list_comparisons", {})
        assert listed.structured_content["comparisons"] == []
        got = await b.call_tool(
            "get_comparison", {"comparison_id": created["comparison"]["id"]}
        )
        assert got.is_error
    owner = (
        await session.exec(select(User).where(User.email == "a@example.com"))
    ).one()
    row = (await session.exec(select(Comparison))).one()
    assert row.user_id == owner.id


def test_tool_is_registered_as_write_llm_tool():
    from app.mcp_server import registry

    registry.load_tool_modules()
    spec = registry.SPECS["create_comparison"]
    assert (
        spec.scope == "write" and spec.calls_llm and spec.rate_limit_per_minute is None
    )


def test_prompt_carries_today_weekday_and_fixed_fields():
    prompt = trip_extraction.user_prompt(
        "下週五去東京", TODAY, Fixed(destination="日本", concerns=["班機延誤"])
    )
    assert "2026-11-19（週四）" in prompt  # 台北時間的今天，解析相對日期用
    assert "destination" in prompt and "concerns" in prompt  # 已指定的欄位不抽取
    assert "下週五去東京" in prompt
    assert "不要輸出任何金額、保費或保額" in trip_extraction.SYSTEM_PROMPT


@pytest.mark.parametrize(
    "request_text",
    ["我想下個月去東京玩五天，擔心生病和班機延誤", "年底想去韓國玩一週", "夏天去日本"],
)
async def test_vague_time_is_never_guessed(
    session: AsyncSession,
    endpoint: McpEndpoint,
    write_token: str,
    llm: FakeExtractor,
    request_text: str,
):
    """原話沒有明確的日子時，LLM 猜的出發日不算：回 needs_more_info、不建立（實測發現的缺口）。"""
    llm.output = {
        "destination": "日本",
        "depart_date": "2026-12-01",  # LLM 猜的
        "stated_days": 5,
        "concerns": ["突然生病就醫", "班機延誤"],
    }
    before = await count(session)
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c, request_text)
    assert out["status"] == "needs_more_info"
    assert [m["field"] for m in out["missing"]] == ["depart_date"]
    assert out["understood"]["depart_date"] is None
    assert await count(session) == before


@pytest.mark.parametrize(
    "request_text",
    [
        "12/20 去日本",
        "12 月 20 日出發",
        "下個月 5 號出發",
        "明天出發",
        "後天出發",
        "下週五出發",
        "兩週後出發",
        "10天後出發",
    ],
)
def test_day_level_dates_are_recognised(request_text: str):
    assert trip_extraction.has_day_level_date(request_text)


@pytest.mark.parametrize(
    "request_text",
    ["下個月去東京五天", "年底想去", "夏天去日本", "我想去日本玩 5 天", "下個月初"],
)
def test_vague_times_are_not_day_level(request_text: str):
    assert not trip_extraction.has_day_level_date(request_text)


@pytest.mark.usefixtures("seed")
async def test_overrides_depart_date_does_not_need_day_level_wording(
    endpoint: McpEndpoint, write_token: str, llm: FakeExtractor
):
    """呼叫端用 overrides 明確指定出發日時，原話再模糊也行（日期是客戶端給的，不是 LLM 猜的）。"""
    llm.output = {"destination": "日本", "stated_days": 5, "concerns": ["班機延誤"]}
    async with mcp_client(endpoint, write_token) as c:
        out = await ok(c, "下個月去玩", overrides={"depart_date": "2026-12-20"})
    assert out["status"] == "created"
    assert out["understood"]["depart_date"] == "2026-12-20"
