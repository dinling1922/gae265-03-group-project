"""刪除我的資料（ADR-0009、#29）：刪單份保單、刪全部資料；刪後各相關表查不到殘留，只能刪自己的。"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime

import pytest
from app.api.deps import current_user
from app.api.routes.me import optional_google_client
from app.core.token_cipher import cipher_from_settings
from app.main import app
from app.models import (
    Comparison,
    Conversation,
    GoogleGrant,
    Identity,
    Incident,
    IncidentDocument,
    IncidentTodo,
    Message,
    Policy,
    PolicySubmission,
    User,
)
from httpx import AsyncClient
from sqlmodel import SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.google_support import FakeGoogleOAuthClient, use_google_settings

# 使用者資料會掛在這些表上；刪除後必須全空（測試資料庫每個測試都是乾淨的）
OWNED_TABLES = (
    Policy,
    Incident,
    IncidentTodo,
    IncidentDocument,
    PolicySubmission,
    Conversation,
    Message,
    Comparison,
    GoogleGrant,
)


async def count(session: AsyncSession, model: type[SQLModel]) -> int:
    return (await session.exec(select(func.count()).select_from(model))).one()


async def seed_user(session: AsyncSession, email: str) -> tuple[User, Policy]:
    """一個使用者與一整串資料：保單、事件、待辦、文件、送件、對話與訊息、比較、授權。"""
    user = User(email=email)
    session.add(user)
    await session.flush()
    session.add(Identity(user_id=user.id, provider="dev", provider_user_id=email))
    policy = Policy(user_id=user.id, display_name="保單", plan_id="fubon_advanced")
    session.add(policy)
    await session.flush()
    incident = Incident(policy_id=policy.id, title="班機延誤", description="延誤了")
    session.add(incident)
    await session.flush()
    todo = IncidentTodo(incident_id=incident.id, phase="after_return", title="登機證")
    session.add(todo)
    await session.flush()
    session.add(
        IncidentDocument(
            incident_id=incident.id,
            incident_todo_id=todo.id,
            file_name="a.pdf",
            storage_path="drive-file-id",
            content_type="application/pdf",
            size_bytes=10,
        )
    )
    session.add(
        IncidentDocument(
            policy_id=policy.id,
            file_name="共同.pdf",
            storage_path="drive-file-id-2",
            content_type="application/pdf",
            size_bytes=10,
        )
    )
    session.add(PolicySubmission(policy_id=policy.id, incident_id=incident.id))
    conv = Conversation(
        user_id=user.id,
        policy_id=policy.id,
        kind="incident_qa",
        incident_id=incident.id,
    )
    session.add(conv)
    await session.flush()
    session.add(
        Message(conversation_id=conv.id, seq=1, role="user", content="我的護照號碼")
    )
    session.add(
        Comparison(
            user_id=user.id,
            destination="日本",
            depart_date=date(2026, 12, 1),
            return_date=date(2026, 12, 5),
        )
    )
    session.add(
        GoogleGrant(
            user_id=user.id,
            scopes=["drive.file"],
            status="revoked",
            granted_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return user, policy


@pytest.fixture
async def me(session: AsyncSession) -> AsyncGenerator[tuple[User, Policy]]:
    user, policy = await seed_user(session, "me@example.com")
    app.dependency_overrides[current_user] = lambda: user
    yield user, policy
    app.dependency_overrides.pop(current_user, None)


async def test_delete_policy_removes_everything_under_it(
    client: AsyncClient, session: AsyncSession, me: tuple[User, Policy]
) -> None:
    _, policy = me
    r = await client.delete(f"/policies/{policy.id}")
    assert r.status_code == 204
    for model in (
        Policy,
        Incident,
        IncidentTodo,
        IncidentDocument,
        PolicySubmission,
        Conversation,
        Message,
    ):
        assert await count(session, model) == 0, model.__name__
    # 保單以外的資料不動
    assert await count(session, Comparison) == 1
    assert await count(session, GoogleGrant) == 1


@pytest.mark.usefixtures("me")
async def test_delete_policy_of_other_user_is_404_and_keeps_it(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, other_policy = await seed_user(session, "other@example.com")
    r = await client.delete(f"/policies/{other_policy.id}")
    assert r.status_code == 404
    assert await count(session, Policy) == 2
    assert await count(session, Incident) == 2


@pytest.mark.usefixtures("me")
async def test_delete_unknown_policy_is_404(client: AsyncClient) -> None:
    r = await client.delete(f"/policies/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_delete_my_data_leaves_no_rows_and_keeps_user(
    client: AsyncClient, session: AsyncSession, me: tuple[User, Policy]
) -> None:
    user, _ = me
    other, _ = await seed_user(session, "other@example.com")
    r = await client.delete("/me/data")
    assert r.status_code == 204
    # 自己的資料一列不剩：只剩另一位使用者的那一份
    for model in OWNED_TABLES:
        assert await count(session, model) == (2 if model is IncidentDocument else 1), (
            model.__name__
        )
    mine = (await session.exec(select(Policy).where(Policy.user_id == user.id))).all()
    assert mine == []
    theirs = (
        await session.exec(select(Policy).where(Policy.user_id == other.id))
    ).all()
    assert len(theirs) == 1
    # user 與登入身分保留，使用者仍可登入
    assert await session.get(User, user.id) is not None
    assert (
        await session.exec(select(Identity).where(Identity.user_id == user.id))
    ).first() is not None


@pytest.mark.usefixtures("me")
async def test_delete_my_data_twice_is_ok(client: AsyncClient) -> None:
    assert (await client.delete("/me/data")).status_code == 204
    assert (await client.delete("/me/data")).status_code == 204


async def test_delete_my_data_revokes_google_token_first(
    client: AsyncClient,
    session: AsyncSession,
    me: tuple[User, Policy],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = me
    grant = (
        await session.exec(select(GoogleGrant).where(GoogleGrant.user_id == user.id))
    ).one()
    use_google_settings(monkeypatch)
    grant.status = "active"
    grant.refresh_token_encrypted = cipher_from_settings().encrypt("rt-secret")
    session.add(grant)
    await session.commit()
    fake = FakeGoogleOAuthClient()
    app.dependency_overrides[optional_google_client] = lambda: fake
    try:
        r = await client.delete("/me/data")
    finally:
        app.dependency_overrides.pop(optional_google_client, None)
    assert r.status_code == 204
    assert fake.revoked == ["rt-secret"]
    assert await count(session, GoogleGrant) == 0
