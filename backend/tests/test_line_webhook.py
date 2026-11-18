"""LINE webhook（ADR-0033、#486）：用正確／錯誤的簽章打 `/line/webhook`，假 LINE 客戶端，真實測試資料庫。"""

from collections.abc import AsyncGenerator

import pytest
from app.core.config import settings
from app.line_bot import texts
from app.line_bot.client import HttpLineClient, QuickReply, TextMessage
from app.line_bot.registry import LineContext
from app.line_bot.webhook import build_route
from app.models import LineEvent
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.applications import Starlette

from tests.line_support import (
    FakeLineClient,
    follow_event,
    group_message,
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


# ---- 驗簽與啟用 ----


async def test_valid_signature_is_processed(http: AsyncClient, line: FakeLineClient):
    assert await post(http, user_message("說明", event_id="e1")) == 200
    assert line.texts == [texts.HELP_TEXT]


async def test_wrong_signature_is_rejected(http: AsyncClient, line: FakeLineClient):
    body = webhook_body(user_message("說明", event_id="e1"))
    response = await http.post(
        PATH, content=body, headers=signed_headers(body, "another-secret")
    )
    assert response.status_code == 401
    assert line.replies == []


async def test_missing_signature_is_rejected(http: AsyncClient, line: FakeLineClient):
    body = webhook_body(user_message("說明", event_id="e1"))
    assert (await http.post(PATH, content=body)).status_code == 401
    assert line.replies == []


async def test_tampered_body_is_rejected(http: AsyncClient, line: FakeLineClient):
    body = webhook_body(user_message("說明", event_id="e1"))
    headers = signed_headers(body)
    tampered = body.replace(b"U1", b"U2")
    assert tampered != body
    assert (await http.post(PATH, content=tampered, headers=headers)).status_code == 401
    assert line.replies == []


async def test_returns_404_when_channel_not_configured(
    http: AsyncClient, line: FakeLineClient, monkeypatch: pytest.MonkeyPatch
):
    body = webhook_body(user_message("說明", event_id="e1"))
    for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"):
        use_line_settings(monkeypatch)
        monkeypatch.setattr(settings, name, SecretStr(""))
        response = await http.post(PATH, content=body, headers=signed_headers(body))
        assert response.status_code == 404  # 只設一個也不啟用
    assert line.replies == []


async def test_verify_request_with_no_events_returns_200(
    http: AsyncClient, line: FakeLineClient
):
    assert await post(http) == 200
    assert line.replies == []


async def test_only_post_is_allowed(http: AsyncClient):
    assert (await http.get(PATH)).status_code == 405


# ---- 一對一 ----


async def test_follow_replies_welcome_with_commands(
    http: AsyncClient, line: FakeLineClient
):
    assert await post(http, follow_event(event_id="f1")) == 200
    [(token, [message])] = line.replies
    assert token == "token-f1"
    assert message.text == texts.WELCOME_TEXT
    for command in ("比較", "待辦", "說明", "綁定", "解除綁定"):
        assert command in message.text
    assert [q.text for q in message.quick_replies] == ["比較", "待辦", "說明"]


@pytest.mark.parametrize("text", ["你好", "我想買保險", "", "  比較紀錄  "])
async def test_unknown_text_replies_help(
    http: AsyncClient, line: FakeLineClient, text: str
):
    assert await post(http, user_message(text, event_id="e1")) == 200
    assert line.texts == [texts.HELP_TEXT]


async def test_non_text_message_replies_help(http: AsyncClient, line: FakeLineClient):
    event = user_message("x", event_id="e1")
    event["message"] = {"type": "sticker", "id": "1"}
    assert await post(http, event) == 200
    assert line.texts == [texts.HELP_TEXT]


async def test_unfollow_and_postback_are_ignored(
    http: AsyncClient, line: FakeLineClient
):
    unfollow = {
        "type": "unfollow",
        "webhookEventId": "u1",
        "source": {"type": "user", "userId": "U1"},
    }
    postback = {
        **unfollow,
        "type": "postback",
        "webhookEventId": "p1",
        "replyToken": "t",
    }
    assert await post(http, unfollow, postback) == 200
    assert line.replies == []


# ---- 群組與聊天室 ----


@pytest.mark.parametrize("kind", ["group", "room"])
async def test_group_gets_one_notice_and_no_commands(
    http: AsyncClient, line: FakeLineClient, kind: str
):
    assert await post(http, group_message("比較", event_id="g1", kind=kind)) == 200
    assert await post(http, group_message("待辦", event_id="g2", kind=kind)) == 200
    assert await post(http, group_message("說明", event_id="g3", kind=kind)) == 200
    assert line.texts == [texts.GROUP_NOTICE_TEXT]  # 只回一次，也沒執行任何指令


async def test_each_group_gets_its_own_notice(http: AsyncClient, line: FakeLineClient):
    await post(http, group_message("嗨", event_id="g1", chat_id="C1"))
    await post(http, group_message("嗨", event_id="g2", chat_id="C2"))
    assert line.texts == [texts.GROUP_NOTICE_TEXT] * 2


async def test_join_event_gets_notice_then_messages_stay_silent(
    http: AsyncClient, line: FakeLineClient
):
    join = {
        "type": "join",
        "webhookEventId": "j1",
        "replyToken": "token-j1",
        "source": {"type": "group", "groupId": "C1"},
    }
    await post(http, join)
    await post(http, group_message("說明", event_id="g1"))
    assert line.texts == [texts.GROUP_NOTICE_TEXT]


# ---- 去重 ----


async def test_redelivered_event_is_not_replied_twice(
    http: AsyncClient, line: FakeLineClient, session: AsyncSession
):
    event = user_message("說明", event_id="same")
    assert await post(http, event) == 200
    assert await post(http, event) == 200
    assert len(line.replies) == 1
    count = (await session.exec(select(func.count()).select_from(LineEvent))).one()
    assert count == 1


async def test_same_event_twice_in_one_request_is_replied_once(
    http: AsyncClient, line: FakeLineClient
):
    event = user_message("說明", event_id="same")
    assert await post(http, event, event) == 200
    assert len(line.replies) == 1


# ---- 容錯 ----


async def test_one_failing_event_does_not_affect_others(
    http: AsyncClient, line: FakeLineClient, monkeypatch: pytest.MonkeyPatch
):
    from app.line_bot import registry

    async def boom(_: LineContext) -> None:
        raise RuntimeError("處理器壞了")

    monkeypatch.setitem(registry.COMMANDS, "爆炸", boom)
    status = await post(
        http,
        user_message("爆炸", event_id="e1"),
        user_message("說明", event_id="e2"),
    )
    assert status == 200
    assert line.texts == [texts.HELP_TEXT]


async def test_garbage_body_with_valid_signature_returns_200(
    http: AsyncClient, line: FakeLineClient
):
    body = b"not json"
    response = await http.post(PATH, content=body, headers=signed_headers(body))
    assert response.status_code == 200
    assert line.replies == []


# ---- 擴充介面：新增指令與事件處理器不必改共用檔案 ----


async def test_registered_command_gets_context(
    http: AsyncClient, line: FakeLineClient, monkeypatch: pytest.MonkeyPatch
):
    from app.line_bot import registry

    seen: list[LineContext] = []

    async def echo(ctx: LineContext) -> None:
        seen.append(ctx)
        async with ctx.session_maker() as session:
            assert session is not None
        await ctx.reply(f"收到 {ctx.args}", [QuickReply("好", "說明")])

    monkeypatch.setitem(registry.COMMANDS, "測試", echo)
    await post(http, user_message("測試 abc 123", event_id="e1", user_id="Uabc"))
    [ctx] = seen
    assert (ctx.line_user_id, ctx.text, ctx.args) == ("Uabc", "測試 abc 123", "abc 123")
    assert ctx.event["type"] == "message"
    [(_, [message])] = line.replies
    assert message == TextMessage("收到 abc 123", (QuickReply("好", "說明"),))


async def test_unfollow_handler_receives_event(
    http: AsyncClient, line: FakeLineClient, monkeypatch: pytest.MonkeyPatch
):
    from app.line_bot import registry

    seen: list[str] = []

    async def on_unfollow(ctx: LineContext) -> None:
        seen.append(ctx.line_user_id)

    monkeypatch.setitem(registry.EVENT_HANDLERS, "unfollow", [on_unfollow])
    unfollow = {
        "type": "unfollow",
        "webhookEventId": "u1",
        "source": {"type": "user", "userId": "Ubye"},
    }
    await post(http, unfollow)
    assert seen == ["Ubye"]
    assert line.replies == []


# ---- 真實客戶端的請求格式（不連 LINE：換掉 httpx 傳輸）----


async def test_http_client_sends_reply_request(monkeypatch: pytest.MonkeyPatch):
    import httpx
    from app.line_bot import client as client_module

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    await HttpLineClient("tok").reply(
        "rt", [TextMessage("嗨", (QuickReply("比較", "比較"),))]
    )
    [request] = captured
    assert str(request.url) == "https://api.line.me/v2/bot/message/reply"
    assert request.headers["authorization"] == "Bearer tok"
    import json

    assert json.loads(request.content) == {
        "replyToken": "rt",
        "messages": [
            {
                "type": "text",
                "text": "嗨",
                "quickReply": {
                    "items": [
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "比較",
                                "text": "比較",
                            },
                        }
                    ]
                },
            }
        ],
    }


async def test_http_client_swallows_failures(monkeypatch: pytest.MonkeyPatch):
    import httpx
    from app.line_bot import client as client_module

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == "Bearer bad":
            return httpx.Response(401, json={"message": "bad token"})
        raise httpx.ConnectError("down")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    await HttpLineClient("bad").reply("rt", [TextMessage("嗨")])
    await HttpLineClient("down").reply("rt", [TextMessage("嗨")])  # 不丟例外
