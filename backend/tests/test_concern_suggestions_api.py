"""推薦的在意事項（spec #427）：依旅遊日程用 LLM 挑 3 項，存在比較紀錄，日程或旅行規劃改了就重算。

LLM 用假的函式注入，不打真的 API。
"""

from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.api.deps import current_user
from app.domain.concern_suggestions import get_concern_suggester
from app.ingest.plan_matrix import read_plan_data
from app.main import app
from app.models import User
from app.seed import load_plan_data
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

DEPART = datetime.now(ZoneInfo("Asia/Taipei")).date() + timedelta(days=30)

THREE = [
    {"concern": "突然生病就醫", "reason": "D1 黑門市場吃生蠔，生食容易吃壞肚子"},
    {"concern": "信用卡盜刷", "reason": "D3 心齋橋藥妝大採購，刷卡次數多"},
    {"concern": "不小心弄傷別人或弄壞東西", "reason": "D2 租和服，可能弄壞租借品"},
]


def day(n: int) -> str:
    return (DEPART + timedelta(days=n - 1)).isoformat()


class FakeSuggester:
    """記下被呼叫幾次，回傳設定好的原始輸出；output 是例外時就丟出。"""

    def __init__(self, output: object = None) -> None:
        self.output = {"suggestions": THREE} if output is None else output
        self.calls = 0

    async def __call__(self, *_: object) -> object:
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


@pytest.fixture
def llm() -> Generator[FakeSuggester]:
    fake = FakeSuggester()
    app.dependency_overrides[get_concern_suggester] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_concern_suggester, None)


@pytest.fixture
async def user(session: AsyncSession) -> AsyncGenerator[User]:
    u = User(email="a@example.com")
    session.add(u)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
async def cid(client: AsyncClient, user: User) -> str:  # noqa: ARG001
    response = await client.post(
        "/comparisons",
        json={"destination": "日本", "depart_date": day(1), "return_date": day(5)},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add_activity(client: AsyncClient, cid: str, hour: int = 18) -> str:
    response = await client.post(
        f"/comparisons/{cid}/activities",
        json={
            "activities": [
                {
                    "date": day(1),
                    "hour": hour,
                    "title": "黑門市場吃生蠔",
                    "category": "eat",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["created"][0]["id"]


async def suggest(client: AsyncClient, cid: str) -> list:
    response = await client.post(f"/comparisons/{cid}/concern-suggestions")
    assert response.status_code == 200, response.text
    return response.json()


async def test_empty_itinerary_gets_no_suggestions_without_calling_llm(
    client: AsyncClient, cid: str, llm: FakeSuggester
) -> None:
    assert await suggest(client, cid) == []
    assert llm.calls == 0


async def test_suggestions_are_stored_and_reused(
    client: AsyncClient, cid: str, llm: FakeSuggester
) -> None:
    await add_activity(client, cid)

    assert await suggest(client, cid) == THREE
    assert await suggest(client, cid) == THREE
    assert llm.calls == 1


@pytest.mark.parametrize(
    "output",
    [
        {"suggestions": [*THREE[:2], {"concern": "東西弄丟", "reason": "D1 夜市"}]},
        {"suggestions": [*THREE[:2], THREE[0]]},
        {"suggestions": THREE[:2]},
        {"suggestions": [*THREE, {"concern": "班機延誤", "reason": "D1 搭機"}]},
        RuntimeError("LLM 逾時"),
    ],
    ids=["not-in-list", "duplicate", "two", "four", "llm-error"],
)
async def test_invalid_output_gives_no_suggestions_and_is_not_stored(
    client: AsyncClient, cid: str, llm: FakeSuggester, output: object
) -> None:
    await add_activity(client, cid)
    llm.output = output

    assert await suggest(client, cid) == []
    assert await suggest(client, cid) == []
    assert llm.calls == 2  # 沒存，下次進來再試


async def test_other_users_comparison_is_not_found(
    client: AsyncClient, cid: str, llm: FakeSuggester, session: AsyncSession
) -> None:
    other = User(email="b@example.com")
    session.add(other)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: other

    response = await client.post(f"/comparisons/{cid}/concern-suggestions")
    assert response.status_code == 404
    assert llm.calls == 0


async def _add_more(client: AsyncClient, cid: str, _: str) -> None:
    await add_activity(client, cid, hour=10)


async def _edit(client: AsyncClient, cid: str, aid: str) -> None:
    r = await client.patch(
        f"/comparisons/{cid}/activities/{aid}", json={"title": "錦市場"}
    )
    assert r.status_code == 200, r.text


async def _delete_one(client: AsyncClient, cid: str, aid: str) -> None:
    r = await client.delete(f"/comparisons/{cid}/activities/{aid}")
    assert r.status_code == 204, r.text


async def _clear_then_add(client: AsyncClient, cid: str, _: str) -> None:
    r = await client.delete(f"/comparisons/{cid}/activities")
    assert r.status_code == 200, r.text
    assert await suggest(client, cid) == []  # 清空就失效，不再回存好的結果
    await add_activity(client, cid)


def _patch_trip(**body: object):
    async def run(client: AsyncClient, cid: str, _: str) -> None:
        r = await client.patch(f"/comparisons/{cid}", json=body)
        assert r.status_code == 200, r.text

    return run


@pytest.mark.parametrize(
    "change",
    [
        _add_more,
        _edit,
        _delete_one,
        _clear_then_add,
        _patch_trip(destination="韓國"),
        _patch_trip(city="京都"),
        _patch_trip(depart_date=day(0)),
        _patch_trip(return_date=day(6)),
    ],
    ids=[
        "add",
        "edit",
        "delete",
        "clear",
        "destination",
        "city",
        "depart-date",
        "return-date",
    ],
)
async def test_changing_itinerary_or_trip_recomputes(
    client: AsyncClient, cid: str, llm: FakeSuggester, change
) -> None:
    aid = await add_activity(client, cid)
    await add_activity(client, cid, hour=8)  # 刪掉一筆後日程仍不是空的
    await suggest(client, cid)

    await change(client, cid, aid)
    await suggest(client, cid)
    assert llm.calls == 2


@pytest.mark.usefixtures("plan_data")
async def test_choosing_concerns_keeps_suggestions(
    client: AsyncClient, cid: str, llm: FakeSuggester
) -> None:
    """1-2 按下一步只存在意事項：回到 1-2 不重算。"""
    await add_activity(client, cid)
    await suggest(client, cid)

    r = await client.patch(f"/comparisons/{cid}", json={"concerns": ["班機延誤"]})
    assert r.status_code == 200, r.text
    assert await suggest(client, cid) == THREE
    assert llm.calls == 1


@pytest.fixture
async def plan_data(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())
