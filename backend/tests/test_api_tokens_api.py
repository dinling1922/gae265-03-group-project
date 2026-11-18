"""存取權杖（ADR-0032、#471）：建立／列出／撤銷 API 與驗證函式。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.api.deps import current_user
from app.domain.access_tokens import authenticate_bearer
from app.main import app
from app.models import ApiToken, User
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

URL = "/me/api-tokens"


async def create(client: AsyncClient, name: str = "我的 Claude Desktop") -> dict:
    resp = await client.post(URL, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def row_of(session: AsyncSession, token_id: str) -> ApiToken:
    rows = (
        await session.exec(select(ApiToken).execution_options(populate_existing=True))
    ).all()
    return next(r for r in rows if str(r.id) == token_id)


@pytest.mark.usefixtures("logged_in")
async def test_create_returns_plaintext_once(
    client: AsyncClient, session: AsyncSession
):
    created = await create(client)
    plaintext = created["token"]
    assert plaintext.startswith("hyi_") and len(plaintext) > 40
    assert created["prefix"] == plaintext[:8]
    assert created["status"] == "active"
    assert created["scopes"] == ["read"]

    listed = (await client.get(URL)).json()
    assert len(listed) == 1
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    assert plaintext not in (await client.get(URL)).text

    row = await row_of(session, created["id"])
    assert row.token_hash != plaintext and plaintext not in row.token_hash
    assert len(row.token_hash) == 64


@pytest.mark.usefixtures("logged_in")
async def test_default_expiry_is_90_days(client: AsyncClient):
    created = await create(client)
    expires = datetime.fromisoformat(created["expires_at"])
    delta = expires - datetime.now(UTC)
    assert timedelta(days=89, hours=23) < delta <= timedelta(days=90)


@pytest.mark.usefixtures("logged_in")
async def test_name_required(client: AsyncClient):
    assert (await client.post(URL, json={"name": "  "})).status_code == 422
    assert (await client.post(URL, json={})).status_code == 422


async def test_requires_login(client: AsyncClient):
    assert (await client.get(URL)).status_code == 401
    assert (await client.post(URL, json={"name": "x"})).status_code == 401


async def test_authenticate_returns_user_and_updates_last_used(
    client: AsyncClient, logged_in: User, session: AsyncSession
):
    created = await create(client)
    assert (await row_of(session, created["id"])).last_used_at is None

    identity = await authenticate_bearer(session, created["token"])
    assert identity is not None
    assert identity.user.id == logged_in.id
    assert identity.scopes == frozenset({"read"})
    assert str(identity.token_id) == created["id"]
    assert (await row_of(session, created["id"])).last_used_at is not None
    assert (await client.get(URL)).json()[0]["last_used_at"] is not None


@pytest.mark.usefixtures("logged_in")
async def test_authenticate_rejects_unknown_strings(
    client: AsyncClient, session: AsyncSession
):
    created = await create(client)
    for bad in ("", "abc", "hyi_nope", created["token"][:-1] + "x", created["prefix"]):
        assert await authenticate_bearer(session, bad) is None
    # 失敗不算使用
    assert (await row_of(session, created["id"])).last_used_at is None


@pytest.mark.usefixtures("logged_in")
async def test_expired_token_fails(client: AsyncClient, session: AsyncSession):
    created = await create(client)
    row = await row_of(session, created["id"])
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(row)
    await session.commit()

    assert await authenticate_bearer(session, created["token"]) is None
    assert (await client.get(URL)).json()[0]["status"] == "expired"


@pytest.mark.usefixtures("logged_in")
async def test_revoke_takes_effect_immediately(
    client: AsyncClient, session: AsyncSession
):
    created = await create(client)
    assert await authenticate_bearer(session, created["token"]) is not None

    assert (await client.delete(f"{URL}/{created['id']}")).status_code == 204
    assert await authenticate_bearer(session, created["token"]) is None
    listed = (await client.get(URL)).json()[0]
    assert listed["status"] == "revoked" and listed["revoked_at"] is not None

    # 重複撤銷沒事，也不改第一次的時間
    assert (await client.delete(f"{URL}/{created['id']}")).status_code == 204
    assert (await client.get(URL)).json()[0]["revoked_at"] == listed["revoked_at"]


@pytest.mark.usefixtures("logged_in")
async def test_sixth_active_token_is_rejected(client: AsyncClient):
    for i in range(5):
        await create(client, f"t{i}")
    resp = await client.post(URL, json={"name": "t5"})
    assert resp.status_code == 409
    assert len((await client.get(URL)).json()) == 5


@pytest.mark.usefixtures("logged_in")
async def test_revoked_and_expired_do_not_count_toward_limit(
    client: AsyncClient, session: AsyncSession
):
    tokens = [await create(client, f"t{i}") for i in range(5)]
    await client.delete(f"{URL}/{tokens[0]['id']}")
    row = await row_of(session, tokens[1]["id"])
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    session.add(row)
    await session.commit()

    await create(client, "a")
    await create(client, "b")  # 5 把有效：t2 t3 t4 a b
    assert (await client.post(URL, json={"name": "c"})).status_code == 409


async def test_cannot_list_or_revoke_others_tokens(
    client: AsyncClient, logged_in: User, session: AsyncSession
):
    mine = await create(client, "mine")
    other = User(email="other@example.com")
    session.add(other)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: other

    assert (await client.get(URL)).json() == []
    assert (await client.delete(f"{URL}/{mine['id']}")).status_code == 404
    # 別人的額度不受影響
    for i in range(5):
        await create(client, f"o{i}")

    app.dependency_overrides[current_user] = lambda: logged_in
    assert (await client.get(URL)).json()[0]["status"] == "active"
    assert await authenticate_bearer(session, mine["token"]) is not None
    assert (await client.delete(f"{URL}/{uuid.uuid4()}")).status_code == 404
