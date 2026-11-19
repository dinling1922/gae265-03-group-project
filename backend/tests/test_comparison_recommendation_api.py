"""1-3 推薦方案與比較表（spec #61、spec #457）：存在比較紀錄、讀取時重算卡片、理由與比較表。

選方案的規則與需求等級另見 domain/test_recommendation_pick.py、test_recommendation_need_levels_api.py。
"""

import re
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.api.deps import current_user
from app.domain.benefits import (
    CATEGORY_CONCERN,
    CONCERN_BENEFITS,
    EXPLANATIONS,
    EXTRA_ROW_LABELS,
    Concern,
)
from app.domain.plans import load_plans
from app.ingest.plan_matrix import read_plan_data
from app.main import app
from app.models import Comparison, PlanPremium, User
from app.seed import load_plan_data
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

TODAY = datetime.now(ZoneInfo("Asia/Taipei")).date()


def trip(days: int, concerns: list[str], **overrides: object) -> dict:
    depart = TODAY + timedelta(days=1)
    return {
        "destination": "日本",
        "depart_date": depart.isoformat(),
        "return_date": (depart + timedelta(days=days - 1)).isoformat(),
        "age": 30,
        "concerns": concerns,
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
async def seed(session: AsyncSession) -> None:
    await load_plan_data(session, read_plan_data())


async def create(client: AsyncClient, days: int, concerns: list[str]) -> dict:
    r = await client.post("/comparisons", json=trip(days, concerns))
    assert r.status_code == 201, r.text
    return r.json()


def picked(body: dict) -> tuple:
    rec = body["recommendation"]
    return tuple(
        (rec[k]["plan_code"], rec[k]["sum_insured"], rec[k]["premium"])
        for k in ("recommended", "alternative")
    )


@pytest.mark.usefixtures("user", "seed")
async def test_four_days_card_fraud_first(
    client: AsyncClient, session: AsyncSession
) -> None:
    body = await create(client, 4, ["信用卡盜刷", "突然生病就醫", "行程取消或更改"])
    rec = body["recommendation"]
    assert rec["recommended"]["plan_code"] != rec["alternative"]["plan_code"]
    # 依據：每個在意事項一行，最後一行比保費
    assert [line.split("：")[0] for line in rec["basis"]] == [
        "信用卡盜刷",
        "突然生病就醫",
        "行程取消或更改",
        "保費",
    ]
    assert all(re.search(r"（.+ vs .+）$", line) for line in rec["basis"])
    card = rec["recommended"]
    assert [a["concern"] for a in card["concern_amounts"]] == [
        "信用卡盜刷",
        "突然生病就醫",
        "行程取消或更改",
    ]
    assert card["sum_insured_options"] == sorted(card["sum_insured_options"])
    assert card["sum_insured"] == card["recommended_sum_insured"]
    assert card["recommended_sum_insured"] in card["sum_insured_options"]
    plan = next(p for p in load_plans() if p.id == card["plan_code"])
    assert (card["url"], card["name"], card["company"]) == (
        plan.url,
        plan.name,
        plan.company,
    )

    # 存下的 4 個欄位與回傳一致
    stored = (
        await session.exec(select(Comparison).where(Comparison.id == body["id"]))
    ).one()
    assert (stored.recommended_plan_code, stored.recommended_sum_insured) == (
        card["plan_code"],
        card["sum_insured"],
    )
    assert (stored.alternative_plan_code, stored.alternative_sum_insured) == (
        rec["alternative"]["plan_code"],
        rec["alternative"]["sum_insured"],
    )
    assert stored.recommendation_source == "default"

    # 比較表：在意的列依在意事項順序在前，接著是身故；選了信用卡盜刷才有那一列
    table = rec["table"]
    assert [r["key"] for r in table] == [
        "card_fraud",
        "illness",
        "trip_cancellation",
        "death",
        "accident_medical",
        "flight_delay",
        "baggage_delay",
        "baggage_loss",
        "liability",
    ]
    assert [r["concern"] for r in table[:3]] == [
        "信用卡盜刷",
        "突然生病就醫",
        "行程取消或更改",
    ]
    assert all(r["concern"] is None for r in table[3:])  # 意外醫療不標「你在意」
    assert table[3]["recommended"] == [
        {"name": "身故失能保險金", "amount": card["sum_insured"]}
    ]
    # 海外突發疾病醫療合併一列，分別列出住院、門診、急診
    illness = table[1]
    assert illness["label"] == "海外突發疾病醫療"
    assert {i["name"] for i in illness["recommended"]} <= CONCERN_BENEFITS[
        Concern.MEDICAL
    ]
    assert all(r["explanation"] for r in table)


async def premium_of(session: AsyncSession, plan: str, sum_insured: int, days: int):
    return (
        await session.exec(
            select(PlanPremium.premium).where(
                PlanPremium.plan_code == plan,
                PlanPremium.sum_insured == sum_insured,
                PlanPremium.days == days,
            )
        )
    ).one()


@pytest.mark.usefixtures("user", "seed")
async def test_changing_a_card_sum_changes_only_that_card(
    client: AsyncClient, session: AsyncSession
) -> None:
    body = await create(client, 6, ["班機延誤", "突然生病就醫"])
    before = body["recommendation"]
    alt = before["alternative"]
    new_sum = alt["sum_insured_options"][-1]

    r = await client.patch(
        f"/comparisons/{body['id']}", json={"alternative_sum_insured": new_sum}
    )
    assert r.status_code == 200, r.text
    after = r.json()["recommendation"]
    assert after["recommended"] == before["recommended"]
    assert after["reason"] == before["reason"]
    assert after["basis"] == before["basis"]
    assert after["alternative"]["plan_code"] == alt["plan_code"]
    assert after["alternative"]["sum_insured"] == new_sum
    assert after["alternative"]["recommended_sum_insured"] == alt["sum_insured"]
    assert after["alternative"]["premium"] == await premium_of(
        session, alt["plan_code"], new_sum, 6
    )
    for b, a in zip(before["table"], after["table"], strict=True):
        assert a["recommended"] == b["recommended"]
    death = next(r for r in after["table"] if r["key"] == "death")
    assert death["alternative"] == [{"name": "身故失能保險金", "amount": new_sum}]
    # 重新讀取還是改過的保額
    assert (await client.get(f"/comparisons/{body['id']}")).json()[
        "recommendation"
    ] == after

    r = await client.patch(
        f"/comparisons/{body['id']}", json={"recommended_sum_insured": 2_000_000}
    )
    assert r.status_code == 422
    assert "這個方案沒有 200 萬的報價" in r.text


@pytest.mark.usefixtures("user", "seed")
async def test_changing_sum_without_recommendation_is_rejected(
    client: AsyncClient,
) -> None:
    body = await create(client, 6, [])
    assert body["recommendation"] is None
    r = await client.patch(
        f"/comparisons/{body['id']}", json={"recommended_sum_insured": 3_000_000}
    )
    assert r.status_code == 422


@pytest.mark.usefixtures("user", "seed")
async def test_when_recommendation_is_recomputed(
    client: AsyncClient, session: AsyncSession
) -> None:
    body = await create(client, 6, ["班機延誤", "突然生病就醫"])
    url = f"/comparisons/{body['id']}"
    await client.patch(url, json={"alternative_sum_insured": 10_000_000})

    # 只改目的地、年齡：推薦與已選保額不變
    r = await client.patch(url, json={"age": 50, "destination": "韓國"})
    assert r.json()["recommendation"]["alternative"]["sum_insured"] == 10_000_000

    # 改在意事項：重新推薦，保額回到推薦保額；同一請求的保額被忽略
    r = await client.patch(
        url,
        json={
            "concerns": ["突然生病就醫", "班機延誤"],
            "alternative_sum_insured": 10_000_000,
        },
    )
    alt = r.json()["recommendation"]["alternative"]
    assert alt["sum_insured"] == alt["recommended_sum_insured"] != 10_000_000

    # 改天數：重新推薦
    await client.patch(url, json={"alternative_sum_insured": 10_000_000})
    depart = TODAY + timedelta(days=1)
    r = await client.patch(
        url, json={"return_date": (depart + timedelta(days=3)).isoformat()}
    )
    rec = r.json()["recommendation"]
    assert (
        rec["alternative"]["sum_insured"]
        == rec["alternative"]["recommended_sum_insured"]
    )
    card = rec["recommended"]
    assert card["premium"] == await premium_of(
        session, card["plan_code"], card["sum_insured"], 4
    )

    # 在意事項改成空的：清空推薦
    r = await client.patch(url, json={"concerns": []})
    assert r.json()["recommendation"] is None
    stored = (
        await session.exec(select(Comparison).where(Comparison.id == body["id"]))
    ).one()
    assert (
        stored.recommended_plan_code,
        stored.recommended_sum_insured,
        stored.alternative_plan_code,
        stored.alternative_sum_insured,
        stored.concern_levels,
        stored.recommendation_source,
    ) == (None, None, None, None, None, None)


@pytest.mark.usefixtures("user", "seed")
async def test_without_document_or_card_concerns_there_are_no_such_rows(
    client: AsyncClient,
) -> None:
    body = await create(client, 6, ["行程取消或更改"])
    table = body["recommendation"]["table"]
    keys = [r["key"] for r in table]
    assert "document_loss" not in keys
    assert "card_fraud" not in keys
    assert keys[:2] == ["trip_cancellation", "death"]


def tiny_plan_data() -> dict:
    """兩個方案、5 天：泰安有旅遊責任與住居所第三人責任，安達基礎型都沒有；兩者都沒有意外醫療。"""

    def plan(code: str, premium: int, liability: bool) -> tuple[dict, list[dict]]:
        benefits = [("身故失能保險金", None, 2_000_000)]
        if liability:
            benefits += [
                ("旅遊責任保險(無自負額)", "責任保險", 1_000_000),
                ("旅遊期間住居所第三人責任保險", "責任保險", 500_000),
            ]
        return (
            {
                "plan_code": code,
                "sum_insured": 2_000_000,
                "days": 5,
                "premium": premium,
            },
            [
                {
                    "plan_code": code,
                    "sum_insured": 2_000_000,
                    "name": n,
                    "category": c,
                    "amount": a,
                    "position": i,
                }
                for i, (n, c, a) in enumerate(benefits)
            ],
        )

    (p1, b1), (p2, b2) = plan("taian", 200, True), plan("chubb_basic", 100, False)
    return {"premiums": [p1, p2], "benefits": b1 + b2}


@pytest.mark.usefixtures("user")
async def test_liability_row_and_missing_benefits(
    client: AsyncClient, session: AsyncSession
) -> None:
    await load_plan_data(session, tiny_plan_data())
    body = await create(client, 5, ["不小心弄傷別人或弄壞東西"])
    assert picked(body) == (
        ("taian", 2_000_000, 200),
        ("chubb_basic", 2_000_000, 100),
    )
    table = body["recommendation"]["table"]
    # 責任保險列只列旅遊責任，不含保的是台灣住處的住居所第三人責任
    row = next(r for r in table if r["key"] == "liability")
    assert row["concern"] == "不小心弄傷別人或弄壞東西"
    assert row["recommended"] == [
        {"name": "旅遊責任保險(無自負額)", "amount": 1_000_000}
    ]
    # 沒有的保障是空清單（前端顯示「未列」）
    assert row["alternative"] == []
    medical = next(r for r in table if r["key"] == "accident_medical")
    assert medical["recommended"] == medical["alternative"] == []


@pytest.mark.usefixtures("user", "seed")
async def test_emergency_row_is_added_and_marked_when_chosen(
    client: AsyncClient,
) -> None:
    body = await create(client, 6, ["需要緊急送醫或家屬趕來", "班機延誤"])
    table = body["recommendation"]["table"]
    emergency = table[0]
    assert (emergency["key"], emergency["label"]) == ("emergency", "緊急救援")
    assert emergency["concern"] == "需要緊急送醫或家屬趕來"
    assert not re.search(r"\d", emergency["explanation"])
    names = {i["name"] for i in emergency["recommended"] + emergency["alternative"]}
    assert names <= CONCERN_BENEFITS[Concern.EMERGENCY]
    # 沒選就沒有這一列
    other = await create(client, 6, ["班機延誤"])
    assert "emergency" not in [r["key"] for r in other["recommendation"]["table"]]


def test_every_selectable_plan_has_url() -> None:
    """保單專用方案不會出現在推薦卡片，沒有投保連結。"""
    assert all(p.url.startswith("https://") for p in load_plans() if not p.upload_only)


def test_concern_benefits_fall_in_their_category_rows() -> None:
    for b in read_plan_data()["benefits"]:
        for concern, names in CONCERN_BENEFITS.items():
            if b["name"] in names:
                if concern in EXTRA_ROW_LABELS:
                    assert b["category"] is None, b
                else:
                    assert CATEGORY_CONCERN[b["category"]] == concern, b


def test_explanations_have_no_numbers() -> None:
    assert {"海外突發疾病醫療", "緊急救援"} <= set(EXPLANATIONS)
    for text in EXPLANATIONS.values():
        assert not re.search(r"\d", text), text
