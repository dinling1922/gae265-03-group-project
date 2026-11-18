"""LINE「比較」指令（ADR-0033、#489）：webhook + 假 LINE 客戶端 + 真實 MCP 守門層 + 真實測試資料庫。

數字與名稱的對照基準是網頁 API（GET /comparisons/{id}）與 MCP 工具的輸出。
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from app.api.deps import current_user
from app.ingest.plan_matrix import read_plan_data
from app.line_bot import texts
from app.line_bot.commands import compare as cmd
from app.line_bot.limits import MAX_TEXT_LENGTH
from app.main import app
from app.models import ApiToken, Comparison, LineBinding, McpCallLog, User
from app.seed import load_plan_data
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import line_mcp_support as lm
from tests.test_comparison_recommendation_api import trip

line, endpoint, rate_limit, http, chat = (
    lm.line,
    lm.endpoint,
    lm.rate_limit,
    lm.http,
    lm.chat,
)


@pytest.fixture
async def seed(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())


async def add_comparison(
    session: AsyncSession, user: User, destination: str, n: int, **kw: Any
) -> Comparison:
    """n 越大越新（建立時間遞增）。"""
    c = Comparison(
        user_id=user.id,
        destination=destination,
        depart_date=date(2026, 12, n),
        return_date=date(2026, 12, n + 4),
        created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(hours=n),
        **kw,
    )
    session.add(c)
    await session.commit()
    return c


async def create_via_web(
    client: AsyncClient, user: User, concerns: list[str], days: int = 6
) -> dict:
    """走網頁 API 建立有推薦的比較，回網頁看到的內容。"""
    app.dependency_overrides[current_user] = lambda: user
    try:
        created = await client.post("/comparisons", json=trip(days, concerns))
        assert created.status_code == 201, created.text
        return (await client.get(f"/comparisons/{created.json()['id']}")).json()
    finally:
        app.dependency_overrides.pop(current_user, None)


def money(n: int) -> str:
    return f"{n:,}"


# ---- 列表 ----


async def test_list_shows_newest_first_with_numbers_and_quick_replies(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1, concerns=["班機延誤"])
    await add_comparison(
        session,
        user,
        "韓國",
        10,
        concerns=["班機延誤"],
        recommended_plan_code="fubon_advanced",
        recommended_sum_insured=6_000_000,
        alternative_plan_code="huanan_basic",
        alternative_sum_insured=3_000_000,
    )
    reply = await chat.send("比較")
    assert reply.text.splitlines()[0] == "最近的比較紀錄（最新在前，共 2 筆）："
    assert "1. 韓國 2026-12-10～2026-12-14（5 天）已有推薦" in reply.text
    assert "2. 日本 2026-12-01～2026-12-05（5 天）尚未推薦" in reply.text
    assert [(q.label, q.text) for q in reply.quick_replies] == [
        ("1 韓國 12-10", "比較 1"),
        ("2 日本 12-01", "比較 2"),
    ]
    lm.assert_line_limits(reply)


async def test_list_numbers_match_the_records(chat: lm.Chat, session: AsyncSession):
    """列表的目的地、日期、天數與資料庫的紀錄一致（天數＝回程減出發加 1）。"""
    user = await lm.make_bound_user(session)
    rows = [
        await add_comparison(session, user, d, n)
        for n, d in enumerate(["日本", "韓國", "泰國"], 1)
    ]
    reply = await chat.say("比較")
    for i, row in enumerate(reversed(rows), 1):
        days = (row.return_date - row.depart_date).days + 1
        assert (
            f"{i}. {row.destination} {row.depart_date}～{row.return_date}（{days} 天）"
            in reply
        )


async def test_list_caps_at_limit_and_notes_more(chat: lm.Chat, session: AsyncSession):
    user = await lm.make_bound_user(session)
    for n in range(1, 16):
        await add_comparison(session, user, f"地點{n}", min(n, 20))
    reply = await chat.send("比較")
    assert f"共 {cmd.LIST_LIMIT} 筆" in reply.text
    assert cmd.LIST_MORE_NOTE.format(limit=cmd.LIST_LIMIT) in reply.text
    assert len(reply.quick_replies) == cmd.LIST_LIMIT <= 13
    assert "地點15" in reply.text and "地點5 " not in reply.text  # 最新的 10 筆：15..6
    lm.assert_line_limits(reply)


async def test_list_truncates_long_destination_label(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "非常非常長的目的地名稱" * 5, 1)
    reply = await chat.send("比較")
    [quick] = reply.quick_replies
    assert len(quick.label) == 20 and quick.label.endswith("…")
    assert quick.text == "比較 1"  # 送出的指令不截
    lm.assert_line_limits(reply)


async def test_list_text_is_truncated_when_too_long(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    for n in range(1, 11):
        await add_comparison(session, user, "長" * 700, n)
    reply = await chat.send("比較")
    assert len(reply.text) <= MAX_TEXT_LENGTH
    assert reply.text.endswith("請到網頁查看）")
    lm.assert_line_limits(reply)


async def test_empty_state(chat: lm.Chat, session: AsyncSession):
    await lm.make_bound_user(session)
    reply = await chat.send("比較")
    assert reply.text == cmd.EMPTY_TEXT
    assert "網頁" in reply.text


# ---- 單筆 ----


@pytest.mark.usefixtures("seed")
async def test_detail_complete_matches_web_api(
    chat: lm.Chat, session: AsyncSession, client: AsyncClient
):
    user = await lm.make_bound_user(session)
    web = await create_via_web(client, user, ["班機延誤", "突然生病就醫"])
    reply = await chat.send("比較 1")
    first = reply.text.splitlines()[0]
    assert first == f"日本｜{web['depart_date']}～{web['return_date']}（6 天）"
    assert "在意事項：班機延誤、突然生病就醫" in reply.text
    for label, key in (("推薦方案", "recommended"), ("次推薦", "alternative")):
        card = web["recommendation"][key]
        assert f"{label}：{card['name']}（{card['company']}）" in reply.text
        assert (
            f"保額 {money(card['sum_insured'])} 元／保費 {money(card['premium'])} 元"
            in reply.text
        )
    # 與推薦規則測試鎖住的數字相同
    assert "保費 985 元" in reply.text and "保費 429 元" in reply.text
    assert cmd.DISCLAIMER in reply.text
    assert lm.quick_texts(reply) == ["比較", "待辦", "說明"]
    lm.assert_line_limits(reply)


@pytest.mark.usefixtures("seed")
async def test_quick_reply_text_selects_the_same_record(
    chat: lm.Chat, session: AsyncSession, client: AsyncClient
):
    user = await lm.make_bound_user(session)
    await create_via_web(client, user, ["班機延誤"])
    listing = await chat.send("比較")
    [quick] = listing.quick_replies
    assert (await chat.send(quick.text)).text == (await chat.send("比較 1")).text
    assert "推薦方案" in (await chat.send(quick.text)).text


async def test_detail_incomplete_without_concerns(chat: lm.Chat, session: AsyncSession):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    reply = await chat.say("比較 1")
    assert cmd.INCOMPLETE_HEADER in reply
    assert cmd.MISSING_CONCERNS_TEXT in reply
    assert cmd.INCOMPLETE_FOOTER in reply and "網頁" in reply
    assert "推薦方案：" not in reply and "請使用者" not in reply


async def test_detail_incomplete_with_concerns_but_no_recommendation(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1, concerns=["班機延誤", "證件遺失"])
    reply = await chat.say("比較 1")
    assert "在意事項：班機延誤、證件遺失" in reply
    assert cmd.MISSING_RECOMMENDATION_TEXT in reply


async def test_detail_incomplete_when_quotes_missing(
    chat: lm.Chat, session: AsyncSession
):
    """沒有 seed 報價：推薦的方案已不在報價中。"""
    user = await lm.make_bound_user(session)
    await add_comparison(
        session,
        user,
        "日本",
        1,
        concerns=["班機延誤"],
        recommended_plan_code="fubon_advanced",
        recommended_sum_insured=6_000_000,
        alternative_plan_code="huanan_basic",
        alternative_sum_insured=3_000_000,
    )
    assert cmd.MISSING_STALE_TEXT in await chat.say("比較 1")


async def test_detail_uses_list_order_at_call_time(
    chat: lm.Chat, session: AsyncSession
):
    """編號以呼叫當下的最新列表為準：中間新增一筆，同一個「比較 1」就變成新的那筆。"""
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    assert (await chat.say("比較 1")).startswith("日本｜")
    await add_comparison(session, user, "韓國", 2)
    assert (await chat.say("比較 1")).startswith("韓國｜")
    assert (await chat.say("比較 2")).startswith("日本｜")


@pytest.mark.parametrize("args", ["0", "3", "99", "9" * 30])
async def test_number_out_of_range(chat: lm.Chat, session: AsyncSession, args: str):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    await add_comparison(session, user, "韓國", 2)
    reply = await chat.send(f"比較 {args}")
    assert reply.text.startswith(f"沒有第 {args} 筆")
    assert "目前有 2 筆" in reply.text
    assert len(reply.quick_replies) == 2  # 附列表的快速回覆讓人重選


async def test_number_beyond_listed_limit_mentions_web(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    for n in range(1, 13):
        await add_comparison(session, user, f"地點{n}", n)
    text = await chat.say("比較 11")
    assert text == cmd.OUT_OF_RANGE_BEYOND_TEXT.format(n=11, limit=cmd.LIST_LIMIT)


@pytest.mark.parametrize("args", ["abc", "-1", "1.5", "1 2", "第1筆", "#1"])
async def test_not_a_number_gives_usage(
    chat: lm.Chat, session: AsyncSession, args: str
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    reply = await chat.send(f"比較 {args}")
    assert reply.text == cmd.USAGE_TEXT


async def test_fullwidth_number_is_accepted(chat: lm.Chat, session: AsyncSession):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    assert (await chat.say("比較 １")).startswith("日本｜")


async def test_number_with_empty_list_says_empty(chat: lm.Chat, session: AsyncSession):
    await lm.make_bound_user(session)
    assert await chat.say("比較 1") == cmd.EMPTY_TEXT


async def test_detail_text_is_truncated_when_too_long(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "長" * 6000, 1)
    reply = await chat.send("比較 1")
    assert len(reply.text) <= MAX_TEXT_LENGTH
    assert reply.text.endswith("請到網頁查看）")


# ---- 隔離 ----


@pytest.mark.usefixtures("seed")
async def test_other_users_data_never_appears(
    chat: lm.Chat, session: AsyncSession, client: AsyncClient
):
    mine = await lm.make_bound_user(session, "a@example.com", "U1")
    bob = await lm.make_bound_user(session, "b@example.com", "U2")
    await add_comparison(session, mine, "挪威", 1)
    await add_comparison(session, bob, "祕密的冰島", 2, concerns=["班機延誤"])
    await create_via_web(client, bob, ["證件遺失"])
    seen = [await chat.say("比較"), await chat.say("比較 1"), await chat.say("比較 2")]
    assert all("冰島" not in t and "證件遺失" not in t for t in seen)
    assert "挪威" in seen[0] and "沒有第 2 筆" in seen[2]
    bobs_chat = lm.Chat(chat.http, chat.line, "U2")
    assert "挪威" not in await bobs_chat.say("比較")


# ---- 綁定與錯誤 ----


async def test_not_bound(chat: lm.Chat):
    assert await chat.say("比較") == texts.NOT_BOUND_TEXT
    assert await chat.say("比較 1") == texts.NOT_BOUND_TEXT


async def test_binding_invalid_when_token_revoked(chat: lm.Chat, session: AsyncSession):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    binding = (await session.exec(select(LineBinding))).one()
    token = await session.get(ApiToken, binding.api_token_id)
    assert token is not None
    token.revoked_at = datetime.now(UTC)
    session.add(token)
    await session.commit()
    assert await chat.say("比較") == texts.BINDING_INVALID_TEXT
    assert await chat.say("比較") == texts.NOT_BOUND_TEXT  # 失效的綁定已清掉


@pytest.mark.parametrize("rate_limit", [1])
async def test_rate_limited_on_the_second_call_of_a_detail(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    # 「比較 1」要呼叫兩次工具（列表、單筆），額度只有 1 次：第二次被擋
    assert await chat.say("比較 1") == texts.RATE_LIMITED_TEXT
    assert await chat.say("比較") == texts.RATE_LIMITED_TEXT


async def test_tool_failure_gives_generic_error(
    chat: lm.Chat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """get_comparison 回錯（例如那一筆剛好被刪掉）：回通用錯誤，不洩漏內部訊息。"""
    user = await lm.make_bound_user(session)
    c = await add_comparison(session, user, "日本", 1)
    from app.models import Comparison as C

    real = cmd.call_mcp_tool

    async def delete_then_call(maker, uid, tool, args=None, **kw):
        if tool == "get_comparison":
            row = await session.get(C, c.id)
            assert row is not None
            await session.delete(row)
            await session.commit()
        return await real(maker, uid, tool, args, **kw)

    monkeypatch.setattr(cmd, "call_mcp_tool", delete_then_call)
    assert await chat.say("比較 1") == texts.MCP_ERROR_TEXT


async def test_unexpected_exception_does_not_break_webhook(
    chat: lm.Chat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    from app.line_bot.mcp_client import McpFailure

    await lm.make_bound_user(session)

    async def failing(*_a, **_k):
        return McpFailure("TimeoutError")

    monkeypatch.setattr(cmd, "call_mcp_tool", failing)
    assert await chat.say("比較") == texts.MCP_ERROR_TEXT


async def test_calls_go_through_the_audited_gate(chat: lm.Chat, session: AsyncSession):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    await chat.say("比較 1")
    logs = (await session.exec(select(McpCallLog))).all()
    assert sorted(log.tool_name for log in logs) == [
        "get_comparison",
        "list_comparisons",
    ]
    assert all(log.success for log in logs)
