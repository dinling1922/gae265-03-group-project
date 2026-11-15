"""契約測試的共用夾具：登入的使用者、條款庫、範例事件類型、一張進階型保單。

5、6、7 的負責人換上自己的實作時，只要註冊後設定 CONTRACT_AGENT_5／6／7（見 docs/agents/handoff/README.md），
這個資料夾的測試就會改跑他們的實作。
"""

import os
from collections.abc import AsyncGenerator

import pytest
from app.api.deps import current_user
from app.core.config import settings
from app.main import app
from app.models import Policy, User
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.fixture(autouse=True)
def agent_implementations(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, env in (
        ("AGENT_5_IMPL", "CONTRACT_AGENT_5"),
        ("AGENT_6_IMPL", "CONTRACT_AGENT_6"),
        ("AGENT_7_IMPL", "CONTRACT_AGENT_7"),
    ):
        monkeypatch.setattr(settings, name, os.environ.get(env, "fake"))


@pytest.fixture
async def user(session: AsyncSession) -> AsyncGenerator[User]:
    u = User(email="owner@example.com")
    session.add(u)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
async def catalog_loaded(insurances_loaded: None, incident_catalog: None) -> None:
    """險種資料列與事故類型、待辦範本；要讀條文內容的測試另外要求 `full_catalog`。"""


@pytest.fixture
async def policy(session: AsyncSession, user: User, catalog_loaded: None) -> Policy:
    assert catalog_loaded is None  # 條款庫與範例事件類型要先載入
    p = Policy(
        user_id=user.id,
        display_name="進階型",
        plan_id="fubon_advanced",
        sum_insured=6_000_000,
    )
    session.add(p)
    await session.commit()
    return p
