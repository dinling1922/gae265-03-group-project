"""LINE「待辦」指令（ADR-0033、#490）：webhook + 假 LINE 客戶端 + 真實 MCP 守門層 + 真實測試資料庫。

世界（行程、事件、待辦）沿用 test_mcp_list_todos 的 `build_world`；完成度的對照基準是網頁 GET /trips。
"""

from datetime import UTC, date, datetime

import pytest
from app.api.deps import current_user
from app.core.config import settings
from app.line_bot import flex, texts
from app.line_bot.client import FlexMessage, TextMessage
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
from tests.line_mcp_support import PATH
from tests.line_support import (
    postback_event,
    signed_headers,
    user_message,
    webhook_body,
)
from tests.test_mcp_list_todos import SECRETS, build_world

line, endpoint, rate_limit, http = lm.line, lm.endpoint, lm.rate_limit, lm.http

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


class TodoChat(lm.Chat):
    """待辦現在回 Flex 卡：`send`／`say` 取卡片的 alt_text（與降級純文字同一份文字），空狀態與錯誤仍是純文字。
    卡片本身的結構另外用 `send_card` 檢查。"""

    async def send_any(self, text: str) -> TextMessage | FlexMessage:
        self.n += 1
        body = webhook_body(
            user_message(
                text, event_id=f"{self.user_id}-{self.n}", user_id=self.user_id
            )
        )
        before = len(self.line.replies)
        resp = await self.http.post(PATH, content=body, headers=signed_headers(body))
        assert resp.status_code == 200
        new = self.line.replies[before:]
        assert len(new) == 1 and len(new[0][1]) == 1
        return new[0][1][0]

    async def send(self, text: str) -> TextMessage:
        message = await self.send_any(text)
        if isinstance(message, FlexMessage):
            return TextMessage(message.alt_text, message.quick_replies)
        return message

    async def send_card(self, text: str) -> FlexMessage:
        message = await self.send_any(text)
        assert isinstance(message, FlexMessage)
        return message


@pytest.fixture
def chat(http: AsyncClient, line: lm.FakeLineClient) -> TodoChat:
    return TodoChat(http, line, "U1")


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
    assert reply.quick_replies == ()  # 卡片有按鈕，不重複附快速回覆（#560）
    lm.assert_line_limits(reply)


@pytest.mark.parametrize("arg", ["已完成", "done", "DONE", "  Done "])
async def test_done_lists_only_done(chat: lm.Chat, session: AsyncSession, arg: str):
    await world(session)
    reply = await chat.send(f"待辦 {arg}")
    assert reply.text == DONE_REPLY
    assert reply.quick_replies == ()  # 卡片有按鈕，不重複附快速回覆（#560）


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


async def _add_empty_trip(session: AsyncSession, user_id) -> None:
    """同一位使用者再加一個有已確認事件、但沒有任何計入待辦的行程（0／0）。"""
    policy = Policy(user_id=user_id, display_name="y", plan_id="y", sum_insured=1)
    session.add(policy)
    await session.flush()
    incident = Incident(policy_id=policy.id, title="t", description="d")
    session.add(incident)
    await session.flush()
    session.add(
        Conversation(
            user_id=user_id,
            policy_id=policy.id,
            kind="incident_qa",
            incident_id=incident.id,
        )
    )
    await session.commit()


async def test_card_skips_trips_with_zero_todos(chat: TodoChat, session: AsyncSession):
    """#560：total 為 0 的行程不佔一張待辦卡；有待辦的行程照常。"""
    policy = await world(session)
    await _add_empty_trip(session, policy.user_id)
    card = await chat.send_card("待辦")
    assert card.contents["type"] == "bubble"  # 只剩一個有待辦的行程，不是輪播
    assert "0／0" not in str(card.contents)


async def test_all_trips_zero_todos_replies_plain_text_not_silence(
    chat: TodoChat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await _add_empty_trip(session, user.id)
    message = await chat.send_any("待辦")
    assert isinstance(message, TextMessage)
    assert "已完成 0／0" in message.text
    assert cmd.PENDING_NONE_PARTIAL_TEXT in message.text


async def test_done_card_skips_trips_without_done_items(
    chat: TodoChat, session: AsyncSession
):
    policy = await world(session)
    await _add_empty_trip(session, policy.user_id)
    card = await chat.send_card("待辦 已完成")
    assert card.contents["type"] == "bubble"  # 沒有已完成待辦的行程不顯示空卡


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
    bobs_chat = TodoChat(chat.http, chat.line, "U2")
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


# ---- 待辦卡（Flex，FLEX-3） ----

BASE_URL = "https://app.example.com"


def card_texts(node: object) -> list[str]:
    """卡片裡所有 text 節點的文字（依出現順序）。"""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            found.append(node["text"])
        for value in node.values():
            found += card_texts(value)
    elif isinstance(node, list):
        for item in node:
            found += card_texts(item)
    return found


def card_uris(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "uri":
            found.append(node["uri"])
        for value in node.values():
            found += card_uris(value)
    elif isinstance(node, list):
        for item in node:
            found += card_uris(item)
    return found


async def test_card_shows_trip_progress_incidents_and_todos(
    chat: TodoChat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", BASE_URL)
    await world(session)
    card = await chat.send_card("待辦")
    assert card.contents["type"] == "bubble"
    seen = card_texts(card.contents)
    assert "2026-12-01～2026-12-06" in seen
    assert "已完成 2／6" in seen
    assert "▍就醫（2026-12-04）" in seen
    assert "▍共同文件（整個行程只收一份）" in seen
    assert "‧ 診斷書（事故當下）" in seen
    assert "‧ 理賠申請書（回國後整理）" in seen
    assert "已完成 2／6" in card.alt_text  # alt_text 就是降級純文字
    assert card.quick_replies == ()  # 卡片有按鈕，不重複附快速回覆（#560）
    flex.check_contents(card.contents)


async def test_postback_gives_the_same_card_as_typing(
    chat: TodoChat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", BASE_URL)
    await world(session)
    typed = await chat.send_card("待辦")
    body = webhook_body(postback_event("cmd=todo", event_id="pb-1"))
    resp = await chat.http.post(PATH, content=body, headers=signed_headers(body))
    assert resp.status_code == 200
    pressed = chat.line.replies[-1][1][0]
    assert isinstance(pressed, FlexMessage)
    assert pressed.contents == typed.contents


async def test_done_card_lists_done_items(chat: TodoChat, session: AsyncSession):
    await world(session)
    card = await chat.send_card("待辦 已完成")
    seen = card_texts(card.contents)
    assert "‧ 醫療收據（回國後整理）" in seen
    assert "‧ 診斷書（事故當下）" not in seen
    assert any(t.startswith(cmd.DONE_TITLE) for t in card_texts(card.contents)) or (
        cmd.DONE_TITLE in card.contents["header"]["contents"][0]["text"]
    )


async def test_url_button_points_to_trip_page_when_base_url_is_set(
    chat: TodoChat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", BASE_URL + "/")
    policy = await world(session)
    card = await chat.send_card("待辦")
    assert card_uris(card.contents) == [f"{BASE_URL}/trip/{policy.id}"]
    button = card.contents["footer"]["contents"][0]
    assert button["action"]["label"] == "到網頁處理"


@pytest.mark.parametrize("base", [None, "", "  "])
async def test_no_url_button_without_public_base_url(
    chat: TodoChat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, base
):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", base)
    await world(session)
    card = await chat.send_card("待辦")
    assert card_uris(card.contents) == []
    assert "footer" not in card.contents
    assert "已完成 2／6" in card_texts(card.contents)  # 其餘內容照常


async def test_multiple_trips_make_a_carousel_pointing_to_each_trip(
    chat: TodoChat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", BASE_URL)
    user = await lm.make_bound_user(session)
    await build_world(session, user, "甲")
    await build_world(session, user, "乙")
    card = await chat.send_card("待辦")
    assert card.contents["type"] == "carousel"
    bubbles = card.contents["contents"]
    assert len(bubbles) == 2
    assert card_uris(card.contents) == [f"{BASE_URL}/trip"] * 2
    for b in bubbles:
        assert "已完成 2／6" in card_texts(b)


async def test_more_than_12_trips_are_cut_with_a_notice(
    chat: TodoChat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", BASE_URL)
    user = await lm.make_bound_user(session)
    for i in range(14):
        await build_world(session, user, str(i))
    card = await chat.send_card("待辦")
    assert card.contents["type"] == "carousel"
    bubbles = card.contents["contents"]
    assert len(bubbles) == 12
    assert "還有 3 個行程沒有列出，請到網頁查看全部。" in card_texts(bubbles[-1])
    assert card_uris(bubbles[-1]) == [f"{BASE_URL}/trip"]


async def test_many_todos_are_capped_and_card_stays_within_limits(
    chat: TodoChat, session: AsyncSession
):
    await world(session)
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
    card = await chat.send_card("待辦")
    flex.check_contents(card.contents)
    seen = card_texts(card.contents)
    assert "‧ 診斷書（事故當下）" in seen  # 前面的內容保留
    assert any("請到網頁查看全部" in t and t.startswith("…還有 ") for t in seen)
    assert len([t for t in seen if t.startswith("‧ ")]) <= 15


async def test_many_trips_with_many_todos_still_fit_50kb(
    chat: TodoChat, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    for i in range(13):
        await build_world(session, user, str(i))
    incidents = (await session.exec(select(Incident))).all()
    for incident in incidents:
        session.add_all(
            IncidentTodo(
                incident_id=incident.id,
                phase="after_return",
                title="長" * 200,
                sort_order=100 + i,
            )
            for i in range(30)
        )
    await session.commit()
    message = await chat.send_any("待辦")
    # 要嘛是合規的卡，要嘛降級成純文字；不會沒回應
    if isinstance(message, FlexMessage):
        flex.check_contents(message.contents)
    else:
        assert len(message.text) <= MAX_TEXT_LENGTH


async def test_long_destination_is_cut_in_the_card(
    chat: TodoChat, session: AsyncSession
):
    await world(session)
    incident = (await session.exec(select(Incident))).first()
    assert incident is not None
    incident.destination = "長" * 6000
    session.add(incident)
    await session.commit()
    card = await chat.send_card("待辦")
    flex.check_contents(card.contents)
    assert all(len(t) < 200 for t in card_texts(card.contents))


async def test_line_rejection_degrades_to_the_todo_text(
    chat: TodoChat, session: AsyncSession
):
    await world(session)
    chat.line.reject_flex = True
    reply = await chat.send("待辦")
    assert isinstance(reply, TextMessage)
    assert reply.text == PENDING_REPLY
    assert lm.quick_texts(reply) == ["待辦 已完成", "比較", "說明"]


async def test_empty_and_error_states_stay_plain_text(
    chat: TodoChat, session: AsyncSession
):
    await lm.make_bound_user(session)
    message = await chat.send_any("待辦")
    assert isinstance(message, TextMessage) and message.text == cmd.NO_TRIPS_TEXT
    unbound = TodoChat(chat.http, chat.line, "U9")
    message = await unbound.send_any("待辦")
    assert isinstance(message, TextMessage) and message.text == texts.NOT_BOUND_TEXT
