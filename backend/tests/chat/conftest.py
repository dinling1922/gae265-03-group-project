from collections.abc import AsyncGenerator

import pytest
from app.api.deps import current_user
from app.main import app
from app.models import User
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.fixture
async def catalog_loaded(full_catalog: None) -> None:
    """真實的險種、條文與向量；整個測試階段只匯入一次（見 conftest.py 的 full_catalog）。"""


@pytest.fixture
async def user(session: AsyncSession) -> AsyncGenerator[User]:
    u = User(email="a@example.com")
    session.add(u)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
async def other_user(session: AsyncSession) -> User:
    u = User(email="b@example.com")
    session.add(u)
    await session.commit()
    return u
