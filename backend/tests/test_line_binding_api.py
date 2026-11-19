"""LINE 綁定的管理 API 與綁定邏輯（ADR-0033、#487）：綁定碼、狀態、解除、刪除我的資料。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.core.token_cipher import cipher_from_settings
from app.domain.access_tokens import MAX_ACTIVE_TOKENS, create_token
from app.domain.line_binding import (
    BindOutcome,
    bind_line,
    hash_code,
    issue_code,
)
from app.models import ApiToken, LineBindCode, LineBindFailure, LineBinding, User
from httpx import AsyncClient
from pydantic import SecretStr
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.line_support import use_line_settings

URL = "/me/line-binding"


@pytest.fixture(autouse=True)
def line_on(monkeypatch: pytest.MonkeyPatch) -> None:
    use_line_settings(monkeypatch)


async def new_user(session: AsyncSession, email: str) -> User:
    user = User(email=email)
    session.add(user)
    await session.commit()
    return user


async def bind(session: AsyncSession, user: User, line_user_id: str = "U1"):
    code, _ = await issue_code(session, user.id)
    return await bind_line(session, line_user_id, code, cipher_from_settings())


async def tokens_of(session: AsyncSession, user_id: uuid.UUID) -> list[ApiToken]:
    rows = await session.exec(
        select(ApiToken)
        .where(ApiToken.user_id == user_id)
        .order_by(col(ApiToken.created_at))
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


# ---- 管理 API ----


@pytest.mark.usefixtures("logged_in")
async def test_status_when_unbound(client: AsyncClient):
    body = (await client.get(URL)).json()
    assert body == {"bound": False, "bound_at": None, "code_expires_at": None}


@pytest.mark.usefixtures("logged_in")
async def test_create_code_returns_six_digits_and_ttl(client: AsyncClient):
    resp = await client.post(f"{URL}/code")
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["code"]) == 6 and body["code"].isdigit()
    assert body["ttl_seconds"] == 600
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(minutes=9) < expires - datetime.now(UTC) <= timedelta(minutes=10)


@pytest.mark.usefixtures("logged_in")
async def test_status_shows_code_expiry_but_never_the_code(client: AsyncClient):
    code = (await client.post(f"{URL}/code")).json()
    status = (await client.get(URL)).json()
    assert status["bound"] is False
    assert status["code_expires_at"] == code["expires_at"]
    assert code["code"] not in str(status)


@pytest.mark.usefixtures("logged_in")
async def test_code_is_stored_hashed(client: AsyncClient, session: AsyncSession):
    code = (await client.post(f"{URL}/code")).json()["code"]
    row = (await session.exec(select(LineBindCode))).one()
    assert row.code_hash == hash_code(code)
    assert code not in row.code_hash


@pytest.mark.usefixtures("logged_in")
async def test_regenerating_replaces_the_old_code(
    client: AsyncClient, session: AsyncSession
):
    first = (await client.post(f"{URL}/code")).json()["code"]
    second = (await client.post(f"{URL}/code")).json()["code"]
    assert len((await session.exec(select(LineBindCode))).all()) == 1
    cipher = cipher_from_settings()
    if first != second:
        assert (await bind_line(session, "U1", first, cipher)).outcome == (
            BindOutcome.WRONG
        )
    assert (await bind_line(session, "U1", second, cipher)).outcome == (
        BindOutcome.SUCCESS
    )


@pytest.mark.usefixtures("logged_in")
async def test_endpoints_404_when_line_not_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "LINE_CHANNEL_SECRET", SecretStr(""))
    assert (await client.post(f"{URL}/code")).status_code == 404
    assert (await client.get(URL)).status_code == 404
    assert (await client.delete(URL)).status_code == 404


@pytest.mark.usefixtures("logged_in")
async def test_create_code_503_without_encryption_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", None)
    assert (await client.post(f"{URL}/code")).status_code == 503


async def test_endpoints_require_login(client: AsyncClient):
    assert (await client.get(URL)).status_code == 401
    assert (await client.post(f"{URL}/code")).status_code == 401
    assert (await client.delete(URL)).status_code == 401


async def test_status_and_delete_after_binding(
    client: AsyncClient, session: AsyncSession, logged_in: User
):
    logged_in_id = logged_in.id
    assert (await bind(session, logged_in)).outcome == BindOutcome.SUCCESS
    status = (await client.get(URL)).json()
    assert status["bound"] is True and status["bound_at"] is not None
    assert status["code_expires_at"] is None  # 用掉的碼不算
    assert (await client.delete(URL)).status_code == 204
    assert (await client.get(URL)).json()["bound"] is False
    assert (await session.exec(select(LineBinding))).all() == []
    token = (await tokens_of(session, logged_in_id))[0]
    assert token.revoked_at is not None
    assert (await client.delete(URL)).status_code == 204  # 冪等


async def test_delete_my_data_removes_binding_and_revokes_token(
    client: AsyncClient, session: AsyncSession, logged_in: User
):
    logged_in_id = logged_in.id
    assert (await bind(session, logged_in)).outcome == BindOutcome.SUCCESS
    await client.post(f"{URL}/code")
    assert (await client.delete("/me/data")).status_code == 204
    assert (await session.exec(select(LineBinding))).all() == []
    assert (await session.exec(select(LineBindCode))).all() == []
    assert all(t.revoked_at is not None for t in await tokens_of(session, logged_in_id))


# ---- 綁定邏輯 ----


async def test_bind_success_creates_encrypted_readonly_token(
    session: AsyncSession,
):
    user = await new_user(session, "a@example.com")
    user_id = user.id
    result = await bind(session, user)
    assert result.outcome == BindOutcome.SUCCESS and not result.replaced
    binding = (
        await session.exec(
            select(LineBinding).execution_options(populate_existing=True)
        )
    ).one()
    token = (await tokens_of(session, user_id))[0]
    assert binding.user_id == user.id and binding.line_user_id == "U1"
    assert binding.api_token_id == token.id
    assert token.name == "LINE 綁定" and token.scopes == ["read"]
    plaintext = cipher_from_settings().decrypt(binding.token_encrypted)
    assert plaintext.startswith("hyi_") and plaintext[:8] == token.prefix
    assert plaintext.encode() not in binding.token_encrypted
    assert binding.key_version == 1


async def test_code_is_single_use(session: AsyncSession):
    user = await new_user(session, "a@example.com")
    code, _ = await issue_code(session, user.id)
    cipher = cipher_from_settings()
    assert (await bind_line(session, "U1", code, cipher)).outcome == (
        BindOutcome.SUCCESS
    )
    assert (await bind_line(session, "U2", code, cipher)).outcome == BindOutcome.USED


async def test_expired_code(session: AsyncSession):
    user = await new_user(session, "a@example.com")
    code, _ = await issue_code(session, user.id)
    row = (await session.exec(select(LineBindCode))).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(row)
    await session.commit()
    result = await bind_line(session, "U1", code, cipher_from_settings())
    assert result.outcome == BindOutcome.EXPIRED
    assert (await session.exec(select(LineBinding))).all() == []


async def test_wrong_code(session: AsyncSession):
    user = await new_user(session, "a@example.com")
    code, _ = await issue_code(session, user.id)
    wrong = "000000" if code != "000000" else "111111"
    result = await bind_line(session, "U1", wrong, cipher_from_settings())
    assert result.outcome == BindOutcome.WRONG


async def test_line_already_bound_to_another_account_keeps_code(
    session: AsyncSession,
):
    a = await new_user(session, "a@example.com")
    b = await new_user(session, "b@example.com")
    b_id = b.id
    assert (await bind(session, a, "U1")).outcome == BindOutcome.SUCCESS
    code, _ = await issue_code(session, b_id)
    cipher = cipher_from_settings()
    assert (await bind_line(session, "U1", code, cipher)).outcome == (
        BindOutcome.LINE_TAKEN
    )
    # 沒消耗碼、也不算錯誤；使用者可以先解除再用同一組碼
    assert (await session.exec(select(LineBindFailure))).all() == []
    row = (
        await session.exec(select(LineBindCode).where(LineBindCode.user_id == b_id))
    ).one()
    assert row.used_at is None
    # 另一個 LINE 還是可以用同一組碼
    assert (await bind_line(session, "U2", code, cipher)).outcome == (
        BindOutcome.SUCCESS
    )


async def test_same_line_same_account_is_already_bound(session: AsyncSession):
    user = await new_user(session, "a@example.com")
    user_id = user.id
    assert (await bind(session, user, "U1")).outcome == BindOutcome.SUCCESS
    assert (await bind(session, user, "U1")).outcome == BindOutcome.ALREADY_BOUND
    assert len(await tokens_of(session, user_id)) == 1


async def test_account_rebinding_replaces_old_line_and_revokes_old_token(
    session: AsyncSession,
):
    user = await new_user(session, "a@example.com")
    assert (await bind(session, user, "U1")).outcome == BindOutcome.SUCCESS
    old_token_id = (
        (
            await session.exec(
                select(LineBinding).execution_options(populate_existing=True)
            )
        )
        .one()
        .api_token_id
    )
    result = await bind(session, user, "U2")
    assert result.outcome == BindOutcome.SUCCESS and result.replaced
    binding = (
        await session.exec(
            select(LineBinding).execution_options(populate_existing=True)
        )
    ).one()
    assert binding.line_user_id == "U2" and binding.api_token_id != old_token_id
    old = await session.get(ApiToken, old_token_id, populate_existing=True)
    assert old is not None and old.revoked_at is not None
    new = await session.get(ApiToken, binding.api_token_id, populate_existing=True)
    assert new is not None and new.revoked_at is None


async def test_token_limit_without_binding_is_reported_and_code_kept(
    session: AsyncSession,
):
    user = await new_user(session, "a@example.com")
    user_id = user.id
    for i in range(MAX_ACTIVE_TOKENS):
        await create_token(session, user.id, f"t{i}")
    code, _ = await issue_code(session, user.id)
    cipher = cipher_from_settings()
    assert (await bind_line(session, "U1", code, cipher)).outcome == (
        BindOutcome.TOKEN_LIMIT
    )
    assert (await session.exec(select(LineBinding))).all() == []
    # 使用者撤銷一把後，同一組碼還能用
    first = (await tokens_of(session, user_id))[0]
    first.revoked_at = datetime.now(UTC)
    session.add(first)
    await session.commit()
    assert (await bind_line(session, "U1", code, cipher)).outcome == (
        BindOutcome.SUCCESS
    )


async def test_rebinding_when_old_binding_fills_the_last_slot(session: AsyncSession):
    user = await new_user(session, "a@example.com")
    for i in range(MAX_ACTIVE_TOKENS - 1):
        await create_token(session, user.id, f"t{i}")
    assert (
        await bind(session, user, "U1")
    ).outcome == BindOutcome.SUCCESS  # 剛好第 5 把
    result = await bind(session, user, "U2")  # 要換 LINE：先解除舊的騰出名額
    assert result.outcome == BindOutcome.SUCCESS and result.replaced
    binding = (
        await session.exec(
            select(LineBinding).execution_options(populate_existing=True)
        )
    ).one()
    assert binding.line_user_id == "U2"


# ---- 防暴力猜測 ----


async def test_too_many_wrong_codes_throttle_even_the_right_code(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "LINE_BIND_MAX_FAILURES", 3)
    user = await new_user(session, "a@example.com")
    code, _ = await issue_code(session, user.id)
    wrong = "000000" if code != "000000" else "111111"
    cipher = cipher_from_settings()
    for _ in range(3):
        assert (await bind_line(session, "U9", wrong, cipher)).outcome == (
            BindOutcome.WRONG
        )
    assert (await bind_line(session, "U9", code, cipher)).outcome == (
        BindOutcome.THROTTLED
    )
    # 別的 LINE 帳號不受影響
    assert (await bind_line(session, "U1", code, cipher)).outcome == (
        BindOutcome.SUCCESS
    )


async def test_throttle_ends_after_the_window(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "LINE_BIND_MAX_FAILURES", 2)
    user = await new_user(session, "a@example.com")
    code, _ = await issue_code(session, user.id)
    wrong = "000000" if code != "000000" else "111111"
    cipher = cipher_from_settings()
    for _ in range(2):
        await bind_line(session, "U9", wrong, cipher)
    assert (await bind_line(session, "U9", code, cipher)).outcome == (
        BindOutcome.THROTTLED
    )
    for row in (await session.exec(select(LineBindFailure))).all():
        row.created_at = datetime.now(UTC) - timedelta(minutes=11)
        session.add(row)
    await session.commit()
    assert (await bind_line(session, "U9", code, cipher)).outcome == (
        BindOutcome.SUCCESS
    )


async def test_global_limit_blocks_many_accounts_guessing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "LINE_BIND_GLOBAL_MAX_FAILURES", 4)
    user = await new_user(session, "a@example.com")
    code, _ = await issue_code(session, user.id)
    wrong = "000000" if code != "000000" else "111111"
    cipher = cipher_from_settings()
    for i in range(4):  # 每個帳號只錯一次，單帳號限制擋不到
        await bind_line(session, f"Ux{i}", wrong, cipher)
    assert (await bind_line(session, "Unew", code, cipher)).outcome == (
        BindOutcome.THROTTLED
    )


async def test_used_and_expired_attempts_count_as_failures(session: AsyncSession):
    user = await new_user(session, "a@example.com")
    code, _ = await issue_code(session, user.id)
    cipher = cipher_from_settings()
    await bind_line(session, "U1", code, cipher)
    await bind_line(session, "U2", code, cipher)  # 已用過
    rows = (await session.exec(select(LineBindFailure))).all()
    assert [r.line_user_id for r in rows] == ["U2"]
