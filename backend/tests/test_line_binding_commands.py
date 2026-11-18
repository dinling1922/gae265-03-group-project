"""LINE bot 的綁定、解除綁定、unfollow（ADR-0033、#487）：經 `/line/webhook`，假 LINE 客戶端。"""

import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.core.token_cipher import cipher_from_settings
from app.domain.access_tokens import MAX_ACTIVE_TOKENS, create_token
from app.domain.line_binding import issue_code
from app.line_bot import texts
from app.line_bot.webhook import build_route
from app.models import ApiToken, LineBindCode, LineBinding, User
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.applications import Starlette

from tests.line_support import (
    FakeLineClient,
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


class Chat:
    """替某個 LINE userId 傳訊息，每則用新的事件 id。"""

    def __init__(self, http: AsyncClient, line: FakeLineClient, user_id: str) -> None:
        self.http, self.line, self.user_id, self.n = http, line, user_id, 0

    async def say(self, text: str) -> str:
        self.n += 1
        body = webhook_body(
            user_message(
                text, event_id=f"{self.user_id}-{self.n}", user_id=self.user_id
            )
        )
        before = len(self.line.texts)
        resp = await self.http.post(PATH, content=body, headers=signed_headers(body))
        assert resp.status_code == 200
        replies = self.line.texts[before:]
        assert len(replies) == 1
        return replies[0]

    async def unfollow(self) -> None:
        self.n += 1
        body = webhook_body(
            {
                "type": "unfollow",
                "webhookEventId": f"{self.user_id}-{self.n}",
                "source": {"type": "user", "userId": self.user_id},
            }
        )
        resp = await self.http.post(PATH, content=body, headers=signed_headers(body))
        assert resp.status_code == 200


@pytest.fixture
def chat(http: AsyncClient, line: FakeLineClient) -> Chat:
    return Chat(http, line, "U1")


async def make_user(session: AsyncSession, email: str = "a@example.com") -> uuid.UUID:
    user = User(email=email)
    session.add(user)
    await session.commit()
    return user.id


async def bindings(session: AsyncSession) -> list[LineBinding]:
    return list(
        (
            await session.exec(
                select(LineBinding).execution_options(populate_existing=True)
            )
        ).all()
    )


async def tokens(session: AsyncSession, user_id: uuid.UUID) -> list[ApiToken]:
    return list(
        (
            await session.exec(
                select(ApiToken)
                .where(col(ApiToken.user_id) == user_id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def test_bind_success(chat: Chat, session: AsyncSession, line: FakeLineClient):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    reply = await chat.say(f"綁定 {code}")
    assert reply == texts.BIND_SUCCESS_TEXT
    assert "比較" in reply and "待辦" in reply
    assert [q.text for q in line.replies[-1][1][0].quick_replies] == ["比較", "待辦"]
    [binding] = await bindings(session)
    assert binding.user_id == user_id and binding.line_user_id == "U1"
    plaintext = cipher_from_settings().decrypt(binding.token_encrypted)
    assert plaintext not in reply and code not in reply


async def test_bind_accepts_fullwidth_digits_and_spaces(
    chat: Chat, session: AsyncSession
):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    fullwidth = code.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    assert await chat.say(f"綁定 {fullwidth[:3]} {fullwidth[3:]}") == (
        texts.BIND_SUCCESS_TEXT
    )


@pytest.mark.parametrize("args", ["", " ", "12345", "1234567", "abc123", "12 34"])
async def test_bind_usage_for_bad_format_and_no_failure_counted(
    chat: Chat, session: AsyncSession, args: str
):
    from app.models import LineBindFailure

    assert await chat.say(f"綁定 {args}".rstrip()) == texts.BIND_USAGE_TEXT
    assert (await session.exec(select(LineBindFailure))).all() == []


async def test_bind_wrong_code(chat: Chat, session: AsyncSession):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    wrong = "000000" if code != "000000" else "111111"
    assert await chat.say(f"綁定 {wrong}") == texts.BIND_WRONG_TEXT
    assert await bindings(session) == []


async def test_bind_expired_code(chat: Chat, session: AsyncSession):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    row = (await session.exec(select(LineBindCode))).one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.add(row)
    await session.commit()
    assert await chat.say(f"綁定 {code}") == texts.BIND_EXPIRED_TEXT


async def test_bind_used_code(
    chat: Chat, http: AsyncClient, line: FakeLineClient, session: AsyncSession
):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    assert await chat.say(f"綁定 {code}") == texts.BIND_SUCCESS_TEXT
    other = Chat(http, line, "U2")
    assert await other.say(f"綁定 {code}") == texts.BIND_USED_TEXT


async def test_bind_throttled_after_too_many_wrong_codes(
    chat: Chat, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "LINE_BIND_MAX_FAILURES", 2)
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    wrong = "000000" if code != "000000" else "111111"
    assert await chat.say(f"綁定 {wrong}") == texts.BIND_WRONG_TEXT
    assert await chat.say(f"綁定 {wrong}") == texts.BIND_WRONG_TEXT
    assert await chat.say(f"綁定 {code}") == texts.BIND_THROTTLED_TEXT
    assert await bindings(session) == []


async def test_bind_when_line_bound_to_another_account(
    chat: Chat, session: AsyncSession
):
    a = await make_user(session, "a@example.com")
    b = await make_user(session, "b@example.com")
    code_a, _ = await issue_code(session, a)
    assert await chat.say(f"綁定 {code_a}") == texts.BIND_SUCCESS_TEXT
    code_b, _ = await issue_code(session, b)
    assert await chat.say(f"綁定 {code_b}") == texts.BIND_LINE_TAKEN_TEXT
    # 先解除，同一組碼就能用
    assert await chat.say("解除綁定") == texts.UNBIND_SUCCESS_TEXT
    assert await chat.say(f"綁定 {code_b}") == texts.BIND_SUCCESS_TEXT
    [binding] = await bindings(session)
    assert binding.user_id == b


async def test_bind_same_account_twice(chat: Chat, session: AsyncSession):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    await chat.say(f"綁定 {code}")
    code2, _ = await issue_code(session, user_id)
    assert await chat.say(f"綁定 {code2}") == texts.BIND_ALREADY_TEXT


async def test_account_rebinding_to_another_line(
    chat: Chat, http: AsyncClient, line: FakeLineClient, session: AsyncSession
):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    await chat.say(f"綁定 {code}")
    code2, _ = await issue_code(session, user_id)
    other = Chat(http, line, "U2")
    assert await other.say(f"綁定 {code2}") == texts.BIND_SUCCESS_REPLACED_TEXT
    [binding] = await bindings(session)
    assert binding.line_user_id == "U2"
    # 舊的 LINE 現在是未綁定
    assert await chat.say("解除綁定") == texts.UNBIND_NOT_BOUND_TEXT


async def test_bind_token_limit(chat: Chat, session: AsyncSession):
    user_id = await make_user(session)
    for i in range(MAX_ACTIVE_TOKENS):
        await create_token(session, user_id, f"t{i}")
    code, _ = await issue_code(session, user_id)
    assert await chat.say(f"綁定 {code}") == texts.BIND_TOKEN_LIMIT_TEXT
    assert await bindings(session) == []


async def test_unbind_removes_binding_and_revokes_token(
    chat: Chat, session: AsyncSession
):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    await chat.say(f"綁定 {code}")
    assert await chat.say("解除綁定") == texts.UNBIND_SUCCESS_TEXT
    assert await bindings(session) == []
    [token] = await tokens(session, user_id)
    assert token.revoked_at is not None


async def test_unbind_when_not_bound(chat: Chat):
    assert await chat.say("解除綁定") == texts.UNBIND_NOT_BOUND_TEXT


async def test_unfollow_unbinds_without_reply(
    chat: Chat, session: AsyncSession, line: FakeLineClient
):
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    await chat.say(f"綁定 {code}")
    replies = len(line.replies)
    await chat.unfollow()
    assert len(line.replies) == replies
    assert await bindings(session) == []
    [token] = await tokens(session, user_id)
    assert token.revoked_at is not None


async def test_unfollow_when_not_bound_is_harmless(chat: Chat):
    await chat.unfollow()


async def test_secrets_never_logged_or_replied(
    chat: Chat,
    session: AsyncSession,
    line: FakeLineClient,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    user_id = await make_user(session)
    code, _ = await issue_code(session, user_id)
    wrong = "000000" if code != "000000" else "111111"
    await chat.say(f"綁定 {wrong}")
    await chat.say(f"綁定 {code}")
    await chat.say("解除綁定")
    [token] = await tokens(session, user_id)
    for secret in (code, wrong, token.token_hash):
        assert secret not in caplog.text
        assert all(secret not in t for t in line.texts)
    assert "hyi_" not in caplog.text
    assert all("hyi_" not in t for t in line.texts)
