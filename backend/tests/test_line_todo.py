"""LINE「待辦」指令（ADR-0033、#490）：webhook + 假 LINE 客戶端 + 真實 MCP 守門層 + 真實測試資料庫。

世界（行程、事件、待辦）沿用 test_mcp_list_todos 的 `build_world`；完成度的對照基準是網頁 GET /trips。
"""

from datetime import UTC, date, datetime

import pytest
from app.api.deps import current_user
from app.line_bot import texts
from app.line_bot.commands import todo as cmd
from app.line_bot.limits import MAX_TEXT_LENGTH
from app.main import app
from app.mcp_server.tools.list_todos import CUSTOM_TODO_TITLE
from app.models import (
    ApiToken,
    Conversation,
    Incident,
    IncidentTodo,
    LineBinding,
    McpCallLog,
    Policy,
)
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import line_mcp_support as lm
from tests.test_mcp_list_todos import SECRETS, build_world

line, endpoint, rate_limit, http, chat = (
    lm.line,
    lm.endpoint,
    lm.rate_limit,
    lm.http,
    lm.chat,
)

PENDING_REPLY = """未完成的理賠待辦

【日本 2026-12-01～2026-12-06】已完成 2／6
▍就醫（2026-12-04）
　‧ 診斷書（事故當下）
　‧ 其他待辦（內容請看網頁）（回國後整理）
▍就醫
　‧ 其他待辦（內容請看網頁）（回國後整理）
▍共同文件（整個行程只收一份）
　‧ 理賠申請書（回國後整理）

要勾選或補文件，請回網頁處理。"""

DONE_REPLY = """已完成的理賠待辦

【日本 2026-12-01～2026-12-06】已完成 2／6
▍就醫（2026-12-04）
　‧ 醫療收據（回國後整理）
▍共同文件（整個行程只收一份）
　‧ 機票與登機證（回國後整理）

要勾選或補文件，請回網頁處理。"""


async def world(session: AsyncSession, label: str = "") -> Policy:
    user = await lm.make_bound_user(session)
    return await build_world(session, user, label)


# ---- 內容 ----


async def test_pending_is_grouped_by_trip_then_incident_with_shared_docs(
    chat: lm.Chat, session: AsyncSession
):
    await world(session)
    reply = await chat.send("待辦")
    assert reply.text == PENDING_REPLY
    assert lm.quick_texts(reply) == ["待辦 已完成", "比較", "說明"]
    lm.assert_line_limits(reply)


@pytest.mark.parametrize("arg", ["已完成", "done", "DONE", "  Done "])
async def test_done_lists_only_done(chat: lm.Chat, session: AsyncSession, arg: str):
    await world(session)
    reply = await chat.send(f"待辦 {arg}")
    assert reply.text == DONE_REPLY
    assert lm.quick_texts(reply) == ["待辦", "比較", "說明"]  # 可切回未完成


async def test_pending_and_done_never_overlap(chat: lm.Chat, session: AsyncSession):
    await world(session)
    pending, done = await chat.say("待辦"), await chat.say("待辦 已完成")
    assert "醫療收據" not in pending and "機票與登機證" not in pending
    assert "診斷書" not in done and "理賠申請書" not in done


async def test_phases_are_labelled(chat: lm.Chat, session: AsyncSession):
    await world(session)
    text = await chat.say("待辦")
    assert "診斷書（事故當下）" in text
    assert "理賠申請書（回國後整理）" in text


async def test_todo_without_template_uses_the_tools_fixed_wording(
    chat: lm.Chat, session: AsyncSession
):
    await world(session)
    text = await chat.say("待辦")
    assert text.count(CUSTOM_TODO_TITLE) == 2
    assert SECRETS[3] not in text  # 5 號依使用者敘述加的標題不外流


async def test_progress_matches_trips_page_api(
    chat: lm.Chat, session: AsyncSession, client: AsyncClient
):
    user = await lm.make_bound_user(session)
    await build_world(session, user)
    app.dependency_overrides[current_user] = lambda: user
    try:
        [web] = (await client.get("/trips")).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    expected = f"已完成 {web['todo_done']}／{web['todo_total']}"
    assert expected in await chat.say("待辦")
    assert expected in await chat.say("待辦 已完成")  # 不受篩選影響


async def test_listed_items_add_up_to_the_total(chat: lm.Chat, session: AsyncSession):
    """未完成項目數 + 已完成項目數 = 行程總數（網頁計入的待辦一個不漏、不重複）。"""
    await world(session)
    listed = 0
    for q in ("待辦", "待辦 已完成"):
        listed += (await chat.say(q)).count("　‧ ")
    assert listed == 6


async def test_hidden_things_stay_hidden(chat: lm.Chat, session: AsyncSession):
    """未確認事件、不符保障的文件類待辦、不適用的待辦不列；敏感欄位不出現。"""
    await world(session)
    seen = await chat.say("待辦") + await chat.say("待辦 已完成")
    for hidden in ("還沒確認的文件", "不符保障的文件", "不適用的文件", "依最新狀況"):
        assert hidden not in seen
    for secret in (*SECRETS, "D10", "D03", "http"):
        assert secret not in seen


async def test_multiple_trips_each_with_its_own_progress(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await build_world(session, user, "甲")
    await build_world(session, user, "乙")
    text = await chat.say("待辦")
    assert text.count("【日本 2026-12-01～2026-12-06】已完成 2／6") == 2
    assert text.count("▍共同文件") == 2


# ---- 空狀態 ----


async def test_no_trips(chat: lm.Chat, session: AsyncSession):
    await lm.make_bound_user(session)
    reply = await chat.send("待辦")
    assert reply.text == cmd.NO_TRIPS_TEXT
    assert (await chat.say("待辦 已完成")) == cmd.NO_TRIPS_TEXT


async def test_policy_without_confirmed_incident_is_not_a_trip(
    chat: lm.Chat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    session.add(Policy(user_id=user.id, display_name="x", plan_id="x", sum_insured=1))
    await session.commit()
    assert await chat.say("待辦") == cmd.NO_TRIPS_TEXT


async def test_all_done_is_told_apart_from_no_trips(
    chat: lm.Chat, session: AsyncSession
):
    await world(session)
    for t in (await session.exec(select(IncidentTodo))).all():
        t.status = "done"
        session.add(t)
    await session.commit()
    text = await chat.say("待辦")
    assert cmd.PENDING_NONE_TEXT in text
    assert text != cmd.NO_TRIPS_TEXT
    assert "已完成 " in text and "／" in text  # 仍列出行程的完成度
    assert cmd.TRIP_NONE_TEXT in text


async def test_nothing_done_yet(chat: lm.Chat, session: AsyncSession):
    await world(session)
    for t in (await session.exec(select(IncidentTodo))).all():
        t.status = "pending"
        session.add(t)
    await session.commit()
    text = await chat.say("待辦 已完成")
    assert cmd.DONE_NONE_TEXT in text
    assert "已完成 0／" in text


async def test_trip_with_only_unconfirmed_todos_total_zero_is_not_called_all_done(
    chat: lm.Chat, session: AsyncSession
):
    """行程有已確認事件但沒有任何計入的待辦（0／0）：不說「全部完成」。"""
    user = await lm.make_bound_user(session)
    policy = Policy(user_id=user.id, display_name="x", plan_id="x", sum_insured=1)
    session.add(policy)
    await session.flush()
    incident = Incident(policy_id=policy.id, title="t", description="d")
    session.add(incident)
    await session.flush()
    session.add(
        Conversation(
            user_id=user.id,
            policy_id=policy.id,
            kind="incident_qa",
            incident_id=incident.id,
        )
    )
    await session.commit()
    text = await chat.say("待辦")
    assert "已完成 0／0" in text
    assert cmd.PENDING_NONE_TEXT not in text and cmd.PENDING_NONE_PARTIAL_TEXT in text


# ---- 參數 ----


@pytest.mark.parametrize("arg", ["全部", "pending", "未完成", "已完成 1", "done done"])
async def test_other_arguments_give_usage(
    chat: lm.Chat, session: AsyncSession, arg: str
):
    await world(session)
    reply = await chat.send(f"待辦 {arg}")
    assert reply.text == cmd.USAGE_TEXT
    assert lm.quick_texts(reply) == ["待辦 已完成", "比較", "說明"]


async def test_usage_does_not_call_mcp(chat: lm.Chat, session: AsyncSession):
    await world(session)
    await chat.say("待辦 全部")
    assert (await session.exec(select(McpCallLog))).all() == []


# ---- 長度 ----


async def test_long_text_is_truncated_at_a_line_boundary(
    chat: lm.Chat, session: AsyncSession
):
    policy = await world(session)
    incident = (await session.exec(select(Incident))).first()
    assert incident is not None
    session.add_all(
        IncidentTodo(
            incident_id=incident.id,
            phase="after_return",
            title=f"自訂{i}",
            sort_order=100 + i,
        )
        for i in range(1000)
    )
    await session.commit()
    del policy
    reply = await chat.send("待辦")
    assert len(reply.text) <= MAX_TEXT_LENGTH
    assert reply.text.endswith("請到網頁查看）")
    assert "診斷書（事故當下）" in reply.text  # 前面的內容保留
    lm.assert_line_limits(reply)


async def test_long_destination_still_fits(chat: lm.Chat, session: AsyncSession):
    policy = await world(session)
    del policy
    incident = (await session.exec(select(Incident))).first()
    assert incident is not None
    incident.destination = "長" * 6000
    session.add(incident)
    await session.commit()
    assert len(await chat.say("待辦")) <= MAX_TEXT_LENGTH


# ---- 隔離 ----


async def test_other_users_trips_never_appear(chat: lm.Chat, session: AsyncSession):
    alice = await lm.make_bound_user(session, "a@example.com", "U1")
    bob = await lm.make_bound_user(session, "b@example.com", "U2")
    await build_world(session, alice, "甲")
    bobs = await build_world(session, bob, "乙")
    bobs.end_date = date(2027, 1, 9)
    session.add(bobs)
    await session.commit()
    alices_text = await chat.say("待辦") + await chat.say("待辦 已完成")
    assert "2027-01-09" not in alices_text
    bobs_chat = lm.Chat(chat.http, chat.line, "U2")
    assert "2027-01-09" in await bobs_chat.say("待辦")


# ---- 綁定與錯誤 ----


async def test_not_bound(chat: lm.Chat):
    assert await chat.say("待辦") == texts.NOT_BOUND_TEXT
    assert await chat.say("待辦 已完成") == texts.NOT_BOUND_TEXT


async def test_binding_invalid_when_token_revoked(chat: lm.Chat, session: AsyncSession):
    await world(session)
    binding = (await session.exec(select(LineBinding))).one()
    token = await session.get(ApiToken, binding.api_token_id)
    assert token is not None
    token.revoked_at = datetime.now(UTC)
    session.add(token)
    await session.commit()
    assert await chat.say("待辦") == texts.BINDING_INVALID_TEXT
    assert await chat.say("待辦") == texts.NOT_BOUND_TEXT


@pytest.mark.parametrize("rate_limit", [1])
async def test_rate_limited(chat: lm.Chat, session: AsyncSession):
    await world(session)
    assert (await chat.say("待辦")).startswith(cmd.PENDING_TITLE)
    assert await chat.say("待辦") == texts.RATE_LIMITED_TEXT


async def test_tool_failure_gives_generic_error(
    chat: lm.Chat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    from app.line_bot.mcp_client import McpFailure

    await world(session)

    async def failing(*_a, **_k):
        return McpFailure("TimeoutError 內部訊息")

    monkeypatch.setattr(cmd, "call_mcp_tool", failing)
    reply = await chat.say("待辦")
    assert reply == texts.MCP_ERROR_TEXT and "內部" not in reply


async def test_goes_through_the_audited_gate_read_only(
    chat: lm.Chat, session: AsyncSession
):
    await world(session)
    before = [t.status for t in (await session.exec(select(IncidentTodo))).all()]
    await chat.say("待辦")
    await chat.say("待辦 done")
    logs = (await session.exec(select(McpCallLog))).all()
    assert [(log.tool_name, log.success) for log in logs] == [("list_todos", True)] * 2
    session.expire_all()
    assert [
        t.status for t in (await session.exec(select(IncidentTodo))).all()
    ] == before
