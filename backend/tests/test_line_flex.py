"""LINE Flex 訊息與 postback（ADR-0035、FLEX-1）：簽章正確的 webhook、假 LINE 客戶端、真實測試資料庫。"""

import json
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
import pytest
from app.line_bot import client as client_module
from app.line_bot import flex, texts
from app.line_bot.client import FlexMessage, HttpLineClient, QuickReply, TextMessage
from app.line_bot.registry import LineContext
from app.line_bot.webhook import build_route
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.applications import Starlette

from tests.line_support import (
    FakeLineClient,
    follow_event,
    group_message,
    postback_event,
    signed_headers,
    use_line_settings,
    user_message,
    webhook_body,
)

PATH = "/line/webhook"


@pytest.fixture
def line() -> FakeLineClient:
    return FakeLineClient()


@pytest.fixture
async def http(
    engine: AsyncEngine, line: FakeLineClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncClient]:
    use_line_settings(monkeypatch)
    app = Starlette(
        routes=[
            build_route(
                lambda: AsyncSession(engine, expire_on_commit=False), lambda: line
            )
        ]
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def post(http: AsyncClient, *events: dict) -> int:
    body = webhook_body(*events)
    return (
        await http.post(PATH, content=body, headers=signed_headers(body))
    ).status_code


def button_data(card: FlexMessage) -> dict[str, str]:
    """卡上每個 postback 按鈕：label -> data。"""
    footer = card.contents["footer"]["contents"]
    return {
        b["action"]["label"]: b["action"]["data"]
        for b in footer
        if b["action"]["type"] == "postback"
    }


# ---- 選單卡 ----


async def test_help_replies_menu_card(http: AsyncClient, line: FakeLineClient):
    await post(http, user_message("說明", event_id="e1"))
    [card] = line.flex
    assert card.contents["type"] == "bubble"
    assert card.alt_text == texts.HELP_TEXT
    assert [q.text for q in card.quick_replies] == ["比較", "待辦", "說明"]
    parsed = [flex.parse_postback_data(d) for d in button_data(card).values()]
    assert [p["cmd"] for p in parsed if p] == ["compare", "todo", "help"]


async def test_follow_replies_menu_card_with_welcome(
    http: AsyncClient, line: FakeLineClient
):
    await post(http, follow_event(event_id="f1"))
    [card] = line.flex
    assert card.alt_text == texts.WELCOME_TEXT
    assert "歡迎" in card.contents["header"]["contents"][0]["text"]


# ---- 真實客戶端送出的格式（換掉 httpx 傳輸，不連 LINE）----


def _mock_line(monkeypatch: pytest.MonkeyPatch, status: int) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return captured


async def test_http_client_sends_flex_payload(monkeypatch: pytest.MonkeyPatch):
    captured = _mock_line(monkeypatch, 200)
    card = FlexMessage(
        "摘要", flex.bubble(body=[flex.text("嗨")]), (QuickReply("說明", "說明"),)
    )
    assert await HttpLineClient("tok").reply("rt", [card]) is True
    [message] = json.loads(captured[0].content)["messages"]
    assert message["type"] == "flex"
    assert message["altText"] == "摘要"
    assert message["contents"]["type"] == "bubble"
    assert message["quickReply"]["items"][0]["action"]["text"] == "說明"


async def test_alt_text_is_cut_to_line_limit(monkeypatch: pytest.MonkeyPatch):
    captured = _mock_line(monkeypatch, 200)
    card = FlexMessage("字" * 1000, flex.bubble(body=[flex.text("x")]))
    await HttpLineClient("tok").reply("rt", [card])
    assert len(json.loads(captured[0].content)["messages"][0]["altText"]) == 400


async def test_http_client_reports_failure(monkeypatch: pytest.MonkeyPatch):
    _mock_line(monkeypatch, 400)
    assert await HttpLineClient("t").reply("rt", [TextMessage("嗨")]) is False


# ---- postback ----


async def test_postback_and_typing_give_same_result(
    http: AsyncClient, line: FakeLineClient
):
    await post(http, user_message("說明", event_id="typed"))
    await post(http, postback_event(flex.postback_data("help"), event_id="pressed"))
    typed, pressed = (m for _, [m] in line.replies)
    assert typed == pressed


@pytest.mark.parametrize(
    ("cmd", "typed"), [("compare", "比較"), ("todo", "待辦"), ("help", "說明")]
)
async def test_postback_reaches_same_handler_as_typed_command(
    http: AsyncClient,
    line: FakeLineClient,
    monkeypatch: pytest.MonkeyPatch,
    cmd: str,
    typed: str,
):
    from app.line_bot import registry

    seen: list[LineContext] = []

    async def spy(ctx: LineContext) -> None:
        seen.append(ctx)
        await ctx.reply("ok")

    monkeypatch.setitem(registry.COMMANDS, typed, spy)
    await post(http, user_message(typed, event_id="e1"))
    await post(http, postback_event(f"cmd={cmd}&id=abc-123", event_id="e2"))
    from_text, from_button = seen
    assert from_text.text == from_button.text == typed
    assert from_button.args == ""
    assert from_button.postback == {"id": "abc-123"}
    assert from_text.postback == {}
    assert line.texts == ["ok", "ok"]


async def test_redelivered_postback_is_replied_once(
    http: AsyncClient, line: FakeLineClient
):
    event = postback_event(flex.postback_data("help"), event_id="same")
    assert await post(http, event) == 200
    assert await post(http, event) == 200
    assert len(line.replies) == 1


@pytest.mark.parametrize(
    "data", ["", "garbage", "id=1", "cmd=不存在的指令", "cmd=", "x" * 400]
)
async def test_bad_postback_data_replies_menu(
    http: AsyncClient, line: FakeLineClient, data: str
):
    assert await post(http, postback_event(data, event_id="e1")) == 200
    [card] = line.flex
    assert card.alt_text == texts.HELP_TEXT


async def test_postback_without_data_replies_menu(
    http: AsyncClient, line: FakeLineClient
):
    event = postback_event("x", event_id="e1")
    event["postback"] = {}
    assert await post(http, event) == 200
    assert len(line.flex) == 1


async def test_postback_in_group_is_ignored(http: AsyncClient, line: FakeLineClient):
    event = postback_event(flex.postback_data("help"), event_id="g1")
    event["source"] = {"type": "group", "userId": "U1", "groupId": "C1"}
    assert await post(http, event) == 200
    assert line.replies == []


def test_postback_data_round_trip_and_limits():
    data = flex.postback_data("compare", id="3f2b6a9e-0000-4000-8000-123456789abc")
    assert data == "cmd=compare&id=3f2b6a9e-0000-4000-8000-123456789abc"
    assert flex.parse_postback_data(data) == {
        "cmd": "compare",
        "id": "3f2b6a9e-0000-4000-8000-123456789abc",
    }
    assert flex.parse_postback_data(None) is None
    assert flex.parse_postback_data("a=b") is None
    with pytest.raises(flex.FlexError):
        flex.postback_data("compare", id="x" * 400)


# ---- 降級為純文字 ----


@pytest.fixture
def flex_command(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """登記一個「測試卡」指令，回傳指定內容的 Flex，降級文字固定為「純文字版」。"""
    from app.line_bot import registry

    def register(contents: Any, *, cards: int = 1) -> None:
        async def handler(ctx: LineContext) -> None:
            if cards == 1:
                await ctx.reply_flex(
                    "摘要",
                    contents,
                    fallback_text="純文字版",
                    quick_replies=[QuickReply("說明", "說明")],
                )
            else:
                await ctx.reply_flex_messages(
                    [("摘要", flex.bubble(body=[flex.text("x")]))] * cards,
                    fallback_text="純文字版",
                    quick_replies=[QuickReply("說明", "說明")],
                )

        monkeypatch.setitem(registry.COMMANDS, "測試卡", handler)

    return register


async def test_flex_is_sent_when_within_limits(
    http: AsyncClient, line: FakeLineClient, flex_command: Callable[..., None]
):
    flex_command(flex.bubble(body=[flex.text("內容")]))
    await post(http, user_message("測試卡", event_id="e1"))
    [card] = line.flex
    assert card.alt_text == "摘要"
    assert line.texts == []


def _assert_fallback(line: FakeLineClient) -> None:
    [(token, [message])] = line.replies
    assert token == "token-e1"
    assert message == TextMessage("純文字版", (QuickReply("說明", "說明"),))


async def test_carousel_over_12_degrades_to_text(
    http: AsyncClient, line: FakeLineClient, flex_command: Callable[..., None]
):
    bubbles = [flex.bubble(body=[flex.text(str(i))]) for i in range(13)]
    flex_command({"type": "carousel", "contents": bubbles})  # 繞過 carousel() 的檢查
    await post(http, user_message("測試卡", event_id="e1"))
    _assert_fallback(line)


async def test_flex_over_50kb_degrades_to_text(
    http: AsyncClient, line: FakeLineClient, flex_command: Callable[..., None]
):
    flex_command(flex.bubble(body=[flex.text("字" * 20000)]))  # 約 60 KB
    await post(http, user_message("測試卡", event_id="e1"))
    _assert_fallback(line)


async def test_more_than_5_messages_degrades_to_text(
    http: AsyncClient, line: FakeLineClient, flex_command: Callable[..., None]
):
    flex_command(None, cards=6)
    await post(http, user_message("測試卡", event_id="e1"))
    _assert_fallback(line)


async def test_five_messages_are_sent_as_flex(
    http: AsyncClient, line: FakeLineClient, flex_command: Callable[..., None]
):
    flex_command(None, cards=5)
    await post(http, user_message("測試卡", event_id="e1"))
    assert len(line.flex) == 5
    assert line.texts == []


async def test_builder_failure_degrades_to_text(
    http: AsyncClient, line: FakeLineClient, flex_command: Callable[..., None]
):
    def broken() -> dict:
        raise KeyError("工具輸出少了欄位")

    flex_command(broken)
    await post(http, user_message("測試卡", event_id="e1"))
    _assert_fallback(line)


async def test_line_rejection_degrades_to_text(
    http: AsyncClient, line: FakeLineClient, flex_command: Callable[..., None]
):
    line.reject_flex = True  # 模擬 LINE 對 Flex 回 4xx
    flex_command(flex.bubble(body=[flex.text("內容")]))
    await post(http, user_message("測試卡", event_id="e1"))
    assert len(line.rejected) == 1
    _assert_fallback(line)


async def test_menu_degrades_to_help_text_when_line_rejects(
    http: AsyncClient, line: FakeLineClient
):
    line.reject_flex = True
    await post(http, user_message("說明", event_id="e1"))
    await post(http, follow_event(event_id="e2"))
    assert line.texts == [texts.HELP_TEXT, texts.WELCOME_TEXT]


async def test_second_reply_is_ignored(
    http: AsyncClient, line: FakeLineClient, monkeypatch: pytest.MonkeyPatch
):
    from app.line_bot import registry

    async def twice(ctx: LineContext) -> None:
        await ctx.reply_flex("a", flex.bubble(body=[flex.text("a")]))
        await ctx.reply_flex("b", flex.bubble(body=[flex.text("b")]))

    monkeypatch.setitem(registry.COMMANDS, "兩次", twice)
    await post(http, user_message("兩次", event_id="e1"))
    assert [f.alt_text for f in line.flex] == ["a"]


# ---- 既有純文字行為不變 ----


async def test_unknown_text_and_group_notice_stay_plain_text(
    http: AsyncClient, line: FakeLineClient
):
    await post(http, user_message("你好", event_id="e1"))
    await post(http, group_message("說明", event_id="e2"))
    assert line.texts == [texts.HELP_TEXT, texts.GROUP_NOTICE_TEXT]
    assert line.flex == []


# ---- 積木 ----


def test_building_blocks_enforce_limits():
    with pytest.raises(flex.FlexError):
        flex.carousel([flex.bubble(body=[flex.text("x")])] * 13)
    with pytest.raises(flex.FlexError):
        flex.carousel([])
    with pytest.raises(flex.FlexError):
        flex.bubble()
    with pytest.raises(flex.FlexError):
        flex.postback_button("字" * 21, "cmd=help")
    flex.check_contents(flex.carousel([flex.bubble(body=[flex.text("x")])] * 12))
    with pytest.raises(flex.FlexError):
        flex.check_contents({"type": "text"})
