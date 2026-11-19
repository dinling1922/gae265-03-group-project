"""LINE「比較」輪播卡與推薦詳情卡（ADR-0035、FLEX-2）：簽章正確的 webhook + 假 LINE 客戶端 + 真實 MCP 守門層 + 真實測試資料庫。

內容的對照基準是網頁 API（GET /comparisons/{id}）與資料庫紀錄；純文字降級的內容見 test_line_compare.py。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.ingest.plan_matrix import read_plan_data
from app.line_bot import flex, texts
from app.line_bot.client import FlexMessage, TextMessage
from app.line_bot.commands import compare as cmd
from app.line_bot.mcp_client import McpFailure
from app.models import ApiToken, LineBinding
from app.seed import load_plan_data
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import line_mcp_support as lm
from tests.line_support import (
    FakeLineClient,
    postback_event,
    signed_headers,
    user_message,
    webhook_body,
)
from tests.test_line_compare import add_comparison, create_via_web

endpoint, rate_limit, http, line = lm.endpoint, lm.rate_limit, lm.http, lm.line


@pytest.fixture
async def seed(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())


class Sender:
    """送文字或 postback，回這一次新增的那一則回覆。"""

    def __init__(
        self, http: AsyncClient, line: FakeLineClient, user_id: str = "U1"
    ) -> None:
        self.http, self.line, self.user_id, self.n = http, line, user_id, 0

    async def _send(self, event: dict[str, Any]) -> Any:
        before = len(self.line.messages)
        body = webhook_body(event)
        resp = await self.http.post(lm.PATH, content=body, headers=signed_headers(body))
        assert resp.status_code == 200
        new = self.line.messages[before:]
        assert len(new) == 1
        return new[0]

    async def say(self, text: str) -> Any:
        self.n += 1
        event = user_message(
            text, event_id=f"{self.user_id}-t{self.n}", user_id=self.user_id
        )
        return await self._send(event)

    async def press(self, data: str) -> Any:
        self.n += 1
        event = postback_event(
            data, event_id=f"{self.user_id}-p{self.n}", user_id=self.user_id
        )
        return await self._send(event)


@pytest.fixture
def sender(http: AsyncClient, line: FakeLineClient) -> Sender:
    return Sender(http, line)


def bubbles(message: Any) -> list[dict[str, Any]]:
    assert isinstance(message, FlexMessage)
    assert message.contents["type"] == "carousel"
    return message.contents["contents"]


def all_text(node: Any) -> list[str]:
    """卡片裡所有文字（遞迴）。"""
    if isinstance(node, dict):
        out = [node["text"]] if node.get("type") == "text" else []
        for v in node.values():
            out += all_text(v)
        return out
    if isinstance(node, list):
        return [t for x in node for t in all_text(x)]
    return []


def view_data(bubble: dict[str, Any]) -> str:
    [button] = bubble["footer"]["contents"]
    assert button["action"]["type"] == "postback"
    assert button["action"]["label"] == "看推薦"
    return button["action"]["data"]


def title(bubble: dict[str, Any]) -> str:
    return bubble["header"]["contents"][0]["text"]


def press_data(comparison_id: object) -> str:
    return flex.postback_data("compare", id=str(comparison_id))


# ---- 輪播卡 ----


async def test_compare_replies_carousel_matching_tool_output(
    sender: Sender, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    old = await add_comparison(session, user, "日本", 1, concerns=["班機延誤"])
    new = await add_comparison(
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
    message = await sender.say("比較")
    first, second = bubbles(message)
    assert [title(first), title(second)] == ["韓國", "日本"]  # 最新在前
    assert "2026-12-10～2026-12-14（5 天）" in all_text(first)
    assert "2026-12-01～2026-12-05（5 天）" in all_text(second)
    assert "已有推薦" in all_text(first) and "尚未推薦" in all_text(second)
    assert flex.parse_postback_data(view_data(first)) == {
        "cmd": "compare",
        "id": str(new.id),
    }
    assert flex.parse_postback_data(view_data(second)) == {
        "cmd": "compare",
        "id": str(old.id),
    }
    assert message.alt_text.startswith("最近的比較紀錄")
    assert [q.text for q in message.quick_replies] == ["比較 1", "比較 2"]


async def test_carousel_caps_at_limit_with_web_note(
    sender: Sender, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    for n in range(1, 16):
        await add_comparison(session, user, f"地點{n}", min(n, 20))
    cards = bubbles(await sender.say("比較"))
    assert len(cards) == cmd.LIST_LIMIT + 1 <= flex.MAX_CAROUSEL_BUBBLES
    titles = [title(b) for b in cards[:-1]]
    assert titles[:2] == ["地點15", "地點14"] and "地點5" not in titles
    assert any("網頁" in t for t in all_text(cards[-1]))
    assert "footer" not in cards[-1]  # 提示卡沒有按鈕


async def test_no_note_card_below_limit(sender: Sender, session: AsyncSession):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    assert len(bubbles(await sender.say("比較"))) == 1


async def test_empty_list_replies_text(sender: Sender, session: AsyncSession):
    await lm.make_bound_user(session)
    message = await sender.say("比較")
    assert isinstance(message, TextMessage) and message.text == cmd.EMPTY_TEXT


async def test_menu_button_postback_without_id_replies_carousel(
    sender: Sender, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    assert len(bubbles(await sender.press(flex.postback_data("compare")))) == 1


# ---- 詳情卡 ----


@pytest.mark.usefixtures("seed")
async def test_press_replies_detail_card_matching_web_api(
    sender: Sender, session: AsyncSession, client: AsyncClient
):
    user = await lm.make_bound_user(session)
    web = await create_via_web(client, user, ["班機延誤", "突然生病就醫"])
    carousel = await sender.say("比較")
    card = await sender.press(view_data(bubbles(carousel)[0]))
    assert isinstance(card, FlexMessage) and card.contents["type"] == "bubble"
    shown = all_text(card.contents)
    assert title(card.contents) == "日本"
    assert f"{web['depart_date']}～{web['return_date']}（6 天）" in shown
    assert "在意事項：班機延誤、突然生病就醫" in shown
    for key in ("recommended", "alternative"):
        plan = web["recommendation"][key]
        assert f"{plan['name']}（{plan['company']}）" in shown
        assert f"保額 {plan['sum_insured']:,} 元／保費 {plan['premium']:,} 元" in shown
    assert cmd.DISCLAIMER in shown
    assert "保費 985 元" in card.alt_text  # 通知文字是純文字詳情
    back = card.contents["footer"]["contents"][0]["action"]["data"]
    assert flex.parse_postback_data(back) == {"cmd": "compare"}
    assert [q.text for q in card.quick_replies] == ["比較", "待辦", "說明"]


async def test_press_gets_the_card_record_even_if_order_changed(
    sender: Sender, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    data = view_data(bubbles(await sender.say("比較"))[0])
    await add_comparison(session, user, "韓國", 2)  # 現在列表第 1 筆變成韓國
    card = await sender.press(data)
    assert isinstance(card, FlexMessage) and title(card.contents) == "日本"


async def test_press_incomplete_shows_missing_step(
    sender: Sender, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    row = await add_comparison(session, user, "日本", 1)
    card = await sender.press(press_data(row.id))
    assert isinstance(card, FlexMessage)
    shown = all_text(card.contents)
    assert cmd.MISSING_CONCERNS_TEXT in shown
    assert not any("保額" in t for t in shown)
    assert "請使用者" not in " ".join(shown)


async def test_press_unknown_or_malformed_id_says_not_found(
    sender: Sender, session: AsyncSession
):
    await lm.make_bound_user(session)
    for raw in (str(uuid.uuid4()), "not-a-uuid"):
        message = await sender.press(press_data(raw))
        assert isinstance(message, TextMessage)
        assert message.text == cmd.NOT_FOUND_TEXT


async def test_other_users_id_is_indistinguishable_from_missing(
    http: AsyncClient, line: FakeLineClient, session: AsyncSession
):
    await lm.make_bound_user(session, "a@example.com", "U1")
    bob = await lm.make_bound_user(session, "b@example.com", "U2")
    secret = await add_comparison(session, bob, "祕密的冰島", 2)
    chat = Sender(http, line, "U1")
    theirs = await chat.press(press_data(secret.id))
    missing = await chat.press(press_data(uuid.uuid4()))
    assert isinstance(theirs, TextMessage) and isinstance(missing, TextMessage)
    assert theirs.text == missing.text == cmd.NOT_FOUND_TEXT
    assert "冰島" not in str(line.messages)


# ---- 失敗 ----


async def test_not_bound_list_and_press(sender: Sender):
    for reply in (
        await sender.say("比較"),
        await sender.press(press_data(lm.new_id())),
    ):
        assert isinstance(reply, TextMessage) and reply.text == texts.NOT_BOUND_TEXT


async def test_binding_invalid_on_press(sender: Sender, session: AsyncSession):
    user = await lm.make_bound_user(session)
    row = await add_comparison(session, user, "日本", 1)
    binding = (await session.exec(select(LineBinding))).one()
    token = await session.get(ApiToken, binding.api_token_id)
    assert token is not None
    token.revoked_at = datetime.now(UTC)
    session.add(token)
    await session.commit()
    reply = await sender.press(press_data(row.id))
    assert isinstance(reply, TextMessage) and reply.text == texts.BINDING_INVALID_TEXT


async def test_tool_failure_on_press_gives_generic_error(
    sender: Sender, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    user = await lm.make_bound_user(session)
    row = await add_comparison(session, user, "日本", 1)

    async def failing(*_a: Any, **_k: Any) -> McpFailure:
        return McpFailure("TimeoutError")

    monkeypatch.setattr(cmd, "call_mcp_tool", failing)
    reply = await sender.press(press_data(row.id))
    assert isinstance(reply, TextMessage) and reply.text == texts.MCP_ERROR_TEXT


@pytest.mark.parametrize("rate_limit", [1])
async def test_rate_limited_on_list(sender: Sender, session: AsyncSession):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    await sender.say("比較")
    reply = await sender.say("比較")
    assert isinstance(reply, TextMessage) and reply.text == texts.RATE_LIMITED_TEXT


# ---- 純文字不變 ----


async def test_typed_number_still_replies_plain_text(
    sender: Sender, session: AsyncSession
):
    user = await lm.make_bound_user(session)
    await add_comparison(session, user, "日本", 1)
    reply = await sender.say("比較 1")
    assert isinstance(reply, TextMessage) and reply.text.startswith("日本｜")


# ---- LINE 拒收 Flex 時降級 ----


class TestFallbackWhenLineRejectsFlex:
    @pytest.fixture
    def line(self) -> FakeLineClient:
        return FakeLineClient(reject_flex=True)

    async def test_list_and_detail_fall_back_to_plain_text(
        self, sender: Sender, session: AsyncSession
    ):
        user = await lm.make_bound_user(session)
        row = await add_comparison(session, user, "日本", 1)
        listing = await sender.say("比較")
        assert isinstance(listing, TextMessage)
        assert listing.text.startswith("最近的比較紀錄")
        assert [q.text for q in listing.quick_replies] == ["比較 1"]
        detail = await sender.press(press_data(row.id))
        assert isinstance(detail, TextMessage)
        assert detail.text.startswith("日本｜")
        assert [q.text for q in detail.quick_replies] == ["比較", "待辦", "說明"]
