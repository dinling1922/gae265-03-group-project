"""轉接層呼叫 MCP 的共用函式（ADR-0033、#487）：真實的 /mcp 守門層（行程內 ASGI）、真實測試資料庫。

用 `list_comparisons` 當例子工具。
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta

import pytest
from app.core.token_cipher import cipher_from_settings
from app.domain.access_tokens import MAX_ACTIVE_TOKENS, create_token
from app.domain.line_binding import bind_line, issue_code
from app.line_bot import mcp_client
from app.line_bot.mcp_client import (
    McpBindingInvalid,
    McpFailure,
    McpNotBound,
    McpRateLimited,
    McpSuccess,
    call_mcp_tool,
)
from app.mcp_server.endpoint import McpEndpoint, build_endpoint
from app.models import ApiToken, Comparison, LineBinding, McpCallLog, User
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.line_support import use_line_settings

TOOL = "list_comparisons"


@pytest.fixture(autouse=True)
def line_on(monkeypatch: pytest.MonkeyPatch) -> None:
    use_line_settings(monkeypatch)


@pytest.fixture
def rate_limit() -> int:
    return 1000


@pytest.fixture
async def endpoint(engine: AsyncEngine, rate_limit: int) -> AsyncGenerator[McpEndpoint]:
    ep = build_endpoint(
        lambda: AsyncSession(engine, expire_on_commit=False),
        rate_limit_per_minute=rate_limit,
    )
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with ep.run():
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    await started.wait()
    yield ep
    stop.set()
    await task


@pytest.fixture
def maker(engine: AsyncEngine):
    return lambda: AsyncSession(engine, expire_on_commit=False)


async def bound_user(
    session: AsyncSession, email: str = "a@example.com", line_user_id: str = "U1"
) -> uuid.UUID:
    user = User(email=email)
    session.add(user)
    await session.commit()
    user_id = user.id
    session.add(
        Comparison(
            user_id=user_id,
            destination="日本",
            depart_date=date(2026, 12, 1),
            return_date=date(2026, 12, 5),
        )
    )
    await session.commit()
    code, _ = await issue_code(session, user_id)
    await bind_line(session, line_user_id, code, cipher_from_settings())
    return user_id


async def binding_of(session: AsyncSession) -> LineBinding | None:
    return (
        await session.exec(
            select(LineBinding).execution_options(populate_existing=True)
        )
    ).first()


async def token_row(session: AsyncSession, token_id: uuid.UUID) -> ApiToken:
    token = await session.get(ApiToken, token_id, populate_existing=True)
    assert token is not None
    return token


async def set_expiry(session: AsyncSession, token_id: uuid.UUID, delta: timedelta):
    token = await token_row(session, token_id)
    token.expires_at = datetime.now(UTC) + delta
    session.add(token)
    await session.commit()


async def test_success_returns_structured_content_through_real_gate(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    await bound_user(session)
    result = await call_mcp_tool(maker, "U1", TOOL, {"limit": 5}, asgi=endpoint.asgi)
    assert isinstance(result, McpSuccess)
    [item] = result.content["comparisons"]
    assert item["destination"] == "日本"
    # 守門層照樣稽核
    logs = (await session.exec(select(McpCallLog))).all()
    assert [(log.tool_name, log.success) for log in logs] == [(TOOL, True)]
    # 且用到的是綁定的權杖
    binding = await binding_of(session)
    assert binding is not None
    assert (await token_row(session, binding.api_token_id)).last_used_at is not None


async def test_not_bound(maker, endpoint: McpEndpoint):
    assert await call_mcp_tool(maker, "Unobody", TOOL, asgi=endpoint.asgi) == (
        McpNotBound()
    )


async def test_revoked_token_gives_binding_invalid_and_cleans_up(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    await bound_user(session)
    binding = await binding_of(session)
    assert binding is not None
    token = await token_row(session, binding.api_token_id)
    token.revoked_at = datetime.now(UTC)
    session.add(token)
    await session.commit()
    result = await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    assert result == McpBindingInvalid()
    assert await binding_of(session) is None
    assert await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi) == McpNotBound()


async def test_expired_token_gives_binding_invalid(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    await bound_user(session)
    binding = await binding_of(session)
    assert binding is not None
    await set_expiry(session, binding.api_token_id, timedelta(seconds=-5))
    assert await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi) == (
        McpBindingInvalid()
    )
    assert await binding_of(session) is None


async def test_token_revoked_after_load_is_detected_by_the_gate(
    session: AsyncSession, maker, endpoint: McpEndpoint, monkeypatch: pytest.MonkeyPatch
):
    """載入綁定時還有效、呼叫前一刻被撤銷：守門層回 401，一樣得到「綁定已失效」。"""
    await bound_user(session)
    real_load = mcp_client._load

    async def load_then_revoke(*args, **kwargs):
        cred = await real_load(*args, **kwargs)
        assert isinstance(cred, mcp_client._Credentials)
        async with maker() as other:
            token = await other.get(ApiToken, cred.token_id)
            assert token is not None
            token.revoked_at = datetime.now(UTC)
            other.add(token)
            await other.commit()
        return cred

    monkeypatch.setattr(mcp_client, "_load", load_then_revoke)
    result = await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    assert result == McpBindingInvalid()
    assert await binding_of(session) is None


@pytest.mark.parametrize("rate_limit", [1])
async def test_rate_limited(session: AsyncSession, maker, endpoint: McpEndpoint):
    await bound_user(session)
    assert isinstance(
        await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi), McpSuccess
    )
    # 一次呼叫只送一個 HTTP 請求（見 mcp_client 說明），額度 1 次，第二次被擋
    result = await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    assert isinstance(result, McpRateLimited)
    assert result.retry_after_seconds is not None
    # 限流不影響綁定
    assert await binding_of(session) is not None


async def test_unknown_tool_is_a_failure(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    await bound_user(session)
    result = await call_mcp_tool(maker, "U1", "no_such_tool", asgi=endpoint.asgi)
    assert isinstance(result, McpFailure)
    assert await binding_of(session) is not None


async def test_bad_arguments_is_a_failure(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    await bound_user(session)
    result = await call_mcp_tool(maker, "U1", TOOL, {"limit": 9999}, asgi=endpoint.asgi)
    assert isinstance(result, McpFailure)


# ---- 自動換新 ----


async def test_no_rotation_when_far_from_expiry(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    await bound_user(session)
    before = await binding_of(session)
    assert before is not None
    before_token_id = before.api_token_id
    await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    after = await binding_of(session)
    assert after is not None and after.api_token_id == before_token_id


async def test_rotates_when_under_14_days_and_revokes_old(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    user_id = await bound_user(session)
    before = await binding_of(session)
    assert before is not None
    old_id = before.api_token_id
    await set_expiry(session, old_id, timedelta(days=5))
    result = await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    assert isinstance(result, McpSuccess)
    after = await binding_of(session)
    assert after is not None and after.api_token_id != old_id
    assert (await token_row(session, old_id)).revoked_at is not None
    new = await token_row(session, after.api_token_id)
    assert new.revoked_at is None and new.name == "LINE 綁定" and new.scopes == ["read"]
    assert new.expires_at > datetime.now(UTC) + timedelta(days=80)
    # 存的是新權杖的明文（能解密、前綴對得上）
    assert cipher_from_settings().decrypt(after.token_encrypted)[:8] == new.prefix
    # 之後的呼叫用新的，不再換
    await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    again = await binding_of(session)
    assert again is not None and again.api_token_id == after.api_token_id
    assert user_id == after.user_id


async def test_rotation_skipped_when_token_limit_reached_keeps_old_working(
    session: AsyncSession, maker, endpoint: McpEndpoint
):
    user_id = await bound_user(session)
    for i in range(MAX_ACTIVE_TOKENS - 1):
        await create_token(session, user_id, f"t{i}")
    before = await binding_of(session)
    assert before is not None
    old_id = before.api_token_id
    await set_expiry(session, old_id, timedelta(days=5))
    result = await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    assert isinstance(result, McpSuccess)
    after = await binding_of(session)
    assert after is not None and after.api_token_id == old_id
    assert (await token_row(session, old_id)).revoked_at is None


async def test_rotation_failure_never_loses_the_binding(
    session: AsyncSession, maker, endpoint: McpEndpoint, monkeypatch: pytest.MonkeyPatch
):
    await bound_user(session)
    before = await binding_of(session)
    assert before is not None
    old_id = before.api_token_id
    await set_expiry(session, old_id, timedelta(days=5))

    async def boom(*_args: object, **_kwargs: object):
        raise RuntimeError("資料庫暫時壞掉")

    monkeypatch.setattr(mcp_client, "create_token", boom)
    result = await call_mcp_tool(maker, "U1", TOOL, asgi=endpoint.asgi)
    assert isinstance(result, McpSuccess)  # 沿用還有效的舊權杖
    after = await binding_of(session)
    assert after is not None and after.api_token_id == old_id
    assert (await token_row(session, old_id)).revoked_at is None


async def test_rotation_lost_race_revokes_own_new_token(session: AsyncSession, maker):
    user_id = await bound_user(session)
    binding = await binding_of(session)
    assert binding is not None
    cred = mcp_client._Credentials(
        binding_id=binding.id,
        user_id=user_id,
        token_id=uuid.uuid4(),  # 過期的視角：綁定上的權杖已經不是它
        plaintext="x",
        expires_at=datetime.now(UTC),
    )
    assert await mcp_client._rotate(maker, cred, cipher_from_settings()) is None
    tokens = (
        await session.exec(
            select(ApiToken)
            .where(col(ApiToken.user_id) == user_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert len(tokens) == 2
    unused = [t for t in tokens if t.id != binding.api_token_id]
    assert len(unused) == 1 and unused[0].revoked_at is not None
    after = await binding_of(session)
    assert after is not None and after.api_token_id == binding.api_token_id


def test_default_asgi_is_the_apps_mcp_endpoint():
    from app.main import mcp_endpoint

    assert mcp_client._default_asgi() is mcp_endpoint.asgi
