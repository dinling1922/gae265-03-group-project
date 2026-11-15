"""共用條款庫（conftest.py 的分層 fixture）的隔離：沒要求的測試看到空的，改過的會被清掉重來。

三個測試依檔案順序執行才有意義（前一個動了手腳，後一個確認沒被污染）。
"""

import pytest
from app.models import Insurance, InsuranceRule
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession


async def rule_count(session: AsyncSession) -> int:
    return (await session.exec(select(func.count()).select_from(InsuranceRule))).one()


@pytest.mark.usefixtures("full_catalog")
async def test_改了共用的條文_這個測試自己看得到(session: AsyncSession) -> None:
    assert await rule_count(session) > 100
    first = (await session.exec(select(InsuranceRule).limit(1))).one()
    first.content = "被這個測試改掉"
    session.add(first)
    await session.commit()


@pytest.mark.usefixtures("full_catalog")
async def test_上一個測試改過條文_這裡拿到的是完整的原始資料(
    session: AsyncSession,
) -> None:
    contents = (await session.exec(select(InsuranceRule.content))).all()
    assert "被這個測試改掉" not in contents


async def test_沒有要求共用資料的測試_看到的是空的條款庫(session: AsyncSession) -> None:
    assert await rule_count(session) == 0
    assert (await session.exec(select(func.count()).select_from(Insurance))).one() == 0
