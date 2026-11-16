"""日程活動 API（spec #400、ADR-0031）：批次新增、修改、刪除，以及縮短日期時的確認。"""

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.api.deps import current_user
from app.main import app
from app.models import Comparison, ItineraryActivity, User
from httpx import AsyncClient
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

DEPART = datetime.now(ZoneInfo("Asia/Taipei")).date() + timedelta(days=30)


def day(n: int) -> str:
    """出發日起算的第 n 天（第一天是 1）。"""
    return (DEPART + timedelta(days=n - 1)).isoformat()


def activity(n: int = 1, hour: int = 9, **overrides: object) -> dict:
    return {
        "date": day(n),
        "hour": hour,
        "title": "進進堂",
        "category": "coffee",
    } | overrides


@pytest.fixture
async def user(session: AsyncSession) -> AsyncGenerator[User]:
    u = User(email="a@example.com")
    session.add(u)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
async def comparison(client: AsyncClient, user: User) -> str:  # noqa: ARG001
    """出發日起共 6 天的比較紀錄，回傳 id。"""
    response = await client.post(
        "/comparisons",
        json={
            "destination": "日本",
            "depart_date": day(1),
            "return_date": day(6),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add(client: AsyncClient, cid: str, *items: dict) -> dict:
    response = await client.post(
        f"/comparisons/{cid}/activities", json={"activities": list(items)}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_batch_create_reports_a_taken_slot_and_keeps_the_rest(
    client: AsyncClient, comparison: str
) -> None:
    first = (await add(client, comparison, activity(hour=9)))["created"][0]

    result = await add(
        client,
        comparison,
        activity(hour=10, title="清水寺"),
        activity(hour=9, title="另一間早餐"),  # 與已存的活動撞時段
        activity(hour=11, title="錦市場"),
        activity(hour=11, title="同一批裡重複"),  # 與同一批先出現的撞時段
    )

    assert [a["title"] for a in result["created"]] == ["清水寺", "錦市場"]
    taken_by_first, taken_in_batch = result["failed"]
    assert (taken_by_first["index"], taken_by_first["code"]) == (1, "SLOT_TAKEN")
    assert taken_by_first["occupied_by"] == first["id"]
    assert (taken_in_batch["index"], taken_in_batch["code"]) == (3, "SLOT_TAKEN")
    assert taken_in_batch["occupied_by"] == result["created"][1]["id"]

    listed = (await client.get(f"/comparisons/{comparison}/activities")).json()
    assert [(a["date"], a["hour"]) for a in listed] == [
        (day(1), 9),
        (day(1), 10),
        (day(1), 11),
    ]


async def test_batch_is_limited_to_30_and_fields_are_validated(
    client: AsyncClient, comparison: str
) -> None:
    url = f"/comparisons/{comparison}/activities"
    too_many = [activity(hour=7 + i % 16, n=1 + i // 16) for i in range(31)]
    assert (await client.post(url, json={"activities": too_many})).status_code == 422
    assert (await client.post(url, json={"activities": []})).status_code == 422
    for bad in (
        {"hour": 6},
        {"hour": 23},
        {"duration": 0.25},
        {"duration": 8.5},
        {"duration": 1.3},
        {"category": "shopping"},
        {"title": ""},
        {"latitude": 35.0},  # 只有緯度沒有經度
    ):
        response = await client.post(url, json={"activities": [activity() | bad]})
        assert response.status_code == 422, bad
    assert (await client.get(url)).json() == []


async def test_date_outside_the_trip_is_reported_per_item(
    client: AsyncClient, comparison: str
) -> None:
    result = await add(
        client,
        comparison,
        activity(7),  # 第 7 天，旅行只有 6 天
        activity(0),
        activity(6, hour=22),
    )
    assert [(f["index"], f["code"]) for f in result["failed"]] == [
        (0, "DATE_OUT_OF_RANGE"),
        (1, "DATE_OUT_OF_RANGE"),
    ]
    assert len(result["created"]) == 1


async def test_update_moves_to_a_free_slot_and_rejects_a_taken_one(
    client: AsyncClient, comparison: str
) -> None:
    created = (
        await add(
            client, comparison, activity(hour=9), activity(hour=10, title="清水寺")
        )
    )["created"]
    first, second = created
    url = f"/comparisons/{comparison}/activities/{first['id']}"

    conflict = await client.patch(url, json={"hour": 10})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "SLOT_TAKEN"
    assert conflict.json()["detail"]["occupied_by"] == second["id"]

    moved = await client.patch(url, json={"date": day(2), "hour": 14, "title": "改名"})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["date"], moved.json()["hour"], moved.json()["title"]) == (
        day(2),
        14,
        "改名",
    )

    # 沒搬動時段（只改內容、或原地不動）不算衝突
    same = await client.patch(url, json={"hour": 14, "location": "京都車站"})
    assert same.status_code == 200
    assert same.json()["location"] == "京都車站"

    out_of_range = await client.patch(url, json={"date": day(9)})
    assert out_of_range.status_code == 422


async def test_update_sets_and_clears_coordinates(
    client: AsyncClient, comparison: str
) -> None:
    created = (await add(client, comparison, activity()))["created"][0]
    url = f"/comparisons/{comparison}/activities/{created['id']}"
    with_coords = await client.patch(url, json={"latitude": 35.0, "longitude": 135.7})
    assert (with_coords.json()["latitude"], with_coords.json()["longitude"]) == (
        35.0,
        135.7,
    )
    half = await client.patch(url, json={"longitude": None})
    assert half.status_code == 422
    cleared = await client.patch(url, json={"latitude": None, "longitude": None})
    assert cleared.json()["latitude"] is None


async def test_delete_frees_the_slot_and_missing_activity_is_404(
    client: AsyncClient, comparison: str
) -> None:
    created = (await add(client, comparison, activity()))["created"][0]
    url = f"/comparisons/{comparison}/activities/{created['id']}"
    assert (await client.delete(url)).status_code == 204
    assert (await client.delete(url)).status_code == 404
    assert (await client.patch(url, json={"title": "x"})).status_code == 404
    assert len((await add(client, comparison, activity()))["created"]) == 1


async def test_clear_removes_all_activities_of_this_trip_only(
    client: AsyncClient, comparison: str
) -> None:
    created = await client.post(
        "/comparisons",
        json={"destination": "日本", "depart_date": day(1), "return_date": day(4)},
    )
    assert created.status_code == 201, created.text
    other = created.json()["id"]
    await add(client, comparison, activity(hour=9), activity(hour=10))
    await add(client, other, activity(hour=9))
    url = f"/comparisons/{comparison}/activities"
    assert (await client.delete(url)).json() == {"removed": 2}
    assert (await client.get(url)).json() == []
    assert (await client.delete(url)).json() == {"removed": 0}
    assert len((await client.get(f"/comparisons/{other}/activities")).json()) == 1
    assert len((await add(client, comparison, activity(hour=9)))["created"]) == 1


async def test_someone_elses_comparison_and_activities_are_not_found(
    client: AsyncClient, comparison: str, session: AsyncSession
) -> None:
    created = (await add(client, comparison, activity()))["created"][0]
    other = User(email="b@example.com")
    session.add(other)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: other

    base = f"/comparisons/{comparison}/activities"
    assert (await client.get(base)).status_code == 404
    assert (await client.delete(base)).status_code == 404
    assert (
        await client.post(base, json={"activities": [activity()]})
    ).status_code == 404
    assert (
        await client.patch(f"{base}/{created['id']}", json={"title": "x"})
    ).status_code == 404
    assert (await client.delete(f"{base}/{created['id']}")).status_code == 404


async def test_activity_of_another_comparison_is_not_found(
    client: AsyncClient, comparison: str
) -> None:
    created = (await add(client, comparison, activity()))["created"][0]
    other = (
        await client.post(
            "/comparisons",
            json={"destination": "日本", "depart_date": day(1), "return_date": day(6)},
        )
    ).json()["id"]
    url = f"/comparisons/{other}/activities/{created['id']}"
    assert (await client.delete(url)).status_code == 404


async def test_shortening_dates_needs_confirmation_before_removing_activities(
    client: AsyncClient, comparison: str
) -> None:
    await add(
        client,
        comparison,
        activity(1),
        activity(5, title="第五天"),
        activity(6, title="最後一天"),
    )
    url = f"/comparisons/{comparison}"
    shorter = {"return_date": day(4)}

    unconfirmed = await client.patch(url, json=shorter)
    assert unconfirmed.status_code == 409
    detail = unconfirmed.json()["detail"]
    assert detail["code"] == "WOULD_REMOVE_ACTIVITIES"
    assert [(a["date"], a["hour"], a["title"]) for a in detail["activities"]] == [
        (day(5), 9, "第五天"),
        (day(6), 9, "最後一天"),
    ]
    assert all(a["id"] for a in detail["activities"])
    # 什麼都沒動：日期沒變、活動都還在
    assert (await client.get(url)).json()["return_date"] == day(6)
    assert len((await client.get(f"{url}/activities")).json()) == 3

    confirmed = await client.patch(url, json=shorter | {"confirm_remove": True})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["removed_activity_count"] == 2
    assert confirmed.json()["return_date"] == day(4)
    assert [a["date"] for a in (await client.get(f"{url}/activities")).json()] == [
        day(1)
    ]


async def test_shortening_that_removes_nothing_needs_no_confirmation(
    client: AsyncClient, comparison: str
) -> None:
    await add(client, comparison, activity(2))
    response = await client.patch(
        f"/comparisons/{comparison}", json={"return_date": day(4)}
    )
    assert response.status_code == 200
    assert response.json()["removed_activity_count"] == 0


async def test_extending_dates_keeps_activities(
    client: AsyncClient, comparison: str
) -> None:
    await add(client, comparison, activity(6))
    response = await client.patch(
        f"/comparisons/{comparison}", json={"return_date": day(8)}
    )
    assert response.status_code == 200
    assert len((await client.get(f"/comparisons/{comparison}/activities")).json()) == 1


async def test_deleting_a_comparison_row_cascades_to_its_activities(
    client: AsyncClient, comparison: str, session: AsyncSession
) -> None:
    await add(client, comparison, activity(), activity(hour=10))
    row = (await session.exec(select(Comparison))).one()
    await session.delete(row)
    await session.commit()
    assert (await session.exec(select(col(ItineraryActivity.id)))).all() == []
