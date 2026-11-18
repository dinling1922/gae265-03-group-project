"""1-3 推薦（spec #457、ADR-0032）：LLM 依日程判斷需求等級，程式依 CP 值選方案與保額。

LLM 用假的函式注入，不打真的 API。預期的方案與保費取自共識文件的試算表（5 天、目前 seed）。
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.api.deps import current_user
from app.core.config import settings
from app.domain.need_levels import get_need_leveler, user_prompt
from app.ingest.plan_matrix import read_plan_data
from app.main import app
from app.models import Comparison, ItineraryActivity, User
from app.seed import load_plan_data
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

DEPART = datetime.now(ZoneInfo("Asia/Taipei")).date() + timedelta(days=30)
MEDICAL, FLIGHT, CARD = "突然生病就醫", "班機延誤", "信用卡盜刷"


def day(n: int) -> str:
    return (DEPART + timedelta(days=n - 1)).isoformat()


class FakeLeveler:
    """記下被呼叫幾次，回傳設定好的原始輸出；output 是例外時就丟出，delay 是先等幾秒。"""

    def __init__(self, output: object, delay: float = 0) -> None:
        self.output, self.delay, self.calls = output, delay, 0

    async def __call__(self, *_: object) -> object:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def levels(*items: tuple[str, str, str]) -> dict:
    return {"levels": [{"concern": c, "level": lv, "reason": r} for c, lv, r in items]}


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


@pytest.fixture
def use() -> Generator:
    def install(leveler: FakeLeveler) -> FakeLeveler:
        app.dependency_overrides[get_need_leveler] = lambda: leveler
        return leveler

    yield install
    app.dependency_overrides.pop(get_need_leveler, None)


async def recommend(client: AsyncClient, concerns: list[str], *, ski: bool) -> dict:
    """建立 5 天的比較紀錄（可加一個第 2 天滑雪的活動），再像 1-2 按「下一步」一樣 PATCH。"""
    r = await client.post(
        "/comparisons",
        json={"destination": "日本", "depart_date": day(1), "return_date": day(5)},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    if ski:
        r = await client.post(
            f"/comparisons/{cid}/activities",
            json={
                "activities": [
                    {
                        "date": day(2),
                        "hour": 9,
                        "title": "苗場滑雪",
                        "category": "explore",
                    }
                ]
            },
        )
        assert r.status_code == 200, r.text
    r = await client.patch(f"/comparisons/{cid}", json={"concerns": concerns})
    assert r.status_code == 200, r.text
    return r.json()


def picked(body: dict) -> tuple:
    rec = body["recommendation"]
    return tuple(
        (rec[k]["plan_code"], rec[k]["sum_insured"], rec[k]["premium"])
        for k in ("recommended", "alternative")
    )


@pytest.mark.usefixtures("user", "seed")
async def test_without_activities_uses_default_levels(client: AsyncClient, use) -> None:
    leveler = use(FakeLeveler(AssertionError("不該呼叫")))
    # 試算表第 1 列：生病加強保障、延誤基本保障（正是預設等級）
    body = await recommend(client, [MEDICAL, FLIGHT], ski=False)
    assert leveler.calls == 0
    assert picked(body) == (
        ("huanan_basic", 10_000_000, 921),
        ("huanan_advanced", 8_000_000, 855),
    )
    rec = body["recommendation"]
    assert rec["concern_levels"] == [
        {"concern": MEDICAL, "level": "high", "reason": None},
        {"concern": FLIGHT, "level": "enough", "reason": None},
    ]
    assert rec["recommended"]["recommended_sum_insured"] == 10_000_000
    assert rec["reason"] == (
        "依你排的順序，最在意的「突然生病就醫」需要加強保障、其餘基本保障即可；"
        "在這些需求都滿足的方案中，選保費最低的華南國外旅平險-基礎型。"
    )


@pytest.mark.usefixtures("user", "seed")
@pytest.mark.parametrize(
    ("output", "expected"),
    [
        # 試算表第 2 列：只在意延誤、基本保障
        (
            levels((FLIGHT, "enough", "第 2 天搭機去新潟")),
            (("huanan_basic", 3_000_000, 409), ("taian", 2_000_000, 146)),
        ),
        # 試算表第 3 列：生病、盜刷都只要基本保障
        (
            levels(
                (MEDICAL, "enough", "第 2 天滑雪，天冷容易感冒"),
                (CARD, "enough", "苗場滑雪場刷卡租裝備"),
            ),
            (("huanan_basic", 8_000_000, 778), ("huanan_advanced", 5_000_000, 619)),
        ),
    ],
)
async def test_llm_levels_decide_the_recommendation(
    client: AsyncClient, use, output: dict, expected: tuple
) -> None:
    leveler = use(FakeLeveler(output))
    concerns = [x["concern"] for x in output["levels"]]
    body = await recommend(client, concerns, ski=True)
    assert leveler.calls == 1
    assert picked(body) == expected
    rec = body["recommendation"]
    assert rec["concern_levels"] == output["levels"]
    first_reason = output["levels"][0]["reason"]
    assert rec["reason"].startswith(first_reason)
    assert rec["reason"].endswith(
        f"所以在這些需求都滿足的方案中，選保費最低的{rec['recommended']['name']}。"
    )


@pytest.mark.usefixtures("user", "seed")
@pytest.mark.parametrize(
    "output",
    [
        RuntimeError("LLM 掛了"),
        levels((FLIGHT, "enough", "第 2 天搭機")),  # 少了生病
        levels(
            (MEDICAL, "high", "滑雪"),
            (FLIGHT, "enough", "搭機"),
            (CARD, "enough", "刷卡"),  # 多了沒選的
        ),
        levels((MEDICAL, "high", "滑雪"), (MEDICAL, "enough", "滑雪")),  # 重複
        levels((MEDICAL, "high", "看病可能要花 50 萬"), (FLIGHT, "enough", "搭機")),
        # 9 出自日程的「09:00」、能過數字白名單，但 5 天的行程沒有第 9 天
        levels((MEDICAL, "high", "第 9 天滑雪"), (FLIGHT, "enough", "搭機")),
        levels((MEDICAL, "high", "滑雪"), (FLIGHT, "enough", "1/2 搭機")),  # 寫日期
    ],
)
async def test_bad_llm_output_falls_back_to_default_levels(
    client: AsyncClient, use, output: object
) -> None:
    use(FakeLeveler(output))
    body = await recommend(client, [MEDICAL, FLIGHT], ski=True)
    rec = body["recommendation"]
    assert [x["level"] for x in rec["concern_levels"]] == ["high", "enough"]
    assert all(x["reason"] is None for x in rec["concern_levels"])
    assert picked(body)[0] == ("huanan_basic", 10_000_000, 921)


@pytest.mark.usefixtures("user", "seed")
async def test_slow_llm_falls_back_to_default_levels(
    client: AsyncClient, use, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RECOMMEND_LLM_TIMEOUT", 0.05)
    use(FakeLeveler(levels((FLIGHT, "enough", "第 2 天搭機")), delay=1))
    body = await recommend(client, [FLIGHT], ski=True)
    assert body["recommendation"]["concern_levels"] == [
        {"concern": FLIGHT, "level": "high", "reason": None}
    ]


@pytest.mark.usefixtures("user", "seed")
async def test_itinerary_change_clears_and_next_patch_recommends_again(
    client: AsyncClient, use
) -> None:
    leveler = use(FakeLeveler(levels((FLIGHT, "enough", "第 2 天搭機"))))
    body = await recommend(client, [FLIGHT], ski=True)
    cid = body["id"]
    activity_id = (await client.get(f"/comparisons/{cid}/activities")).json()[0]["id"]

    # 改保額：不重新推薦
    alt_sum = body["recommendation"]["alternative"]["sum_insured_options"][-1]
    await client.patch(f"/comparisons/{cid}", json={"alternative_sum_insured": alt_sum})
    assert leveler.calls == 1

    added = {"date": day(3), "hour": 14, "title": "越後湯澤溫泉", "category": "rest"}
    for change in (
        lambda: client.post(
            f"/comparisons/{cid}/activities", json={"activities": [added]}
        ),
        lambda: client.patch(
            f"/comparisons/{cid}/activities/{activity_id}", json={"title": "苗場纜車"}
        ),
        lambda: client.delete(f"/comparisons/{cid}/activities/{activity_id}"),
    ):
        r = await change()
        assert r.status_code in (200, 204), r.text
        assert (await client.get(f"/comparisons/{cid}")).json()[
            "recommendation"
        ] is None
        r = await client.patch(f"/comparisons/{cid}", json={"concerns": [FLIGHT]})
        assert r.json()["recommendation"] is not None
    # 新增、修改、刪除後都還有活動，每次重算都呼叫 LLM
    assert leveler.calls == 4


@pytest.mark.parametrize(
    ("month", "typhoon"), [(6, False), (7, True), (10, True), (11, False)]
)
def test_user_prompt_marks_days_and_typhoon_season(month: int, typhoon: bool) -> None:
    depart = DEPART.replace(month=month, day=1)
    c = Comparison(
        user_id=uuid.uuid4(),
        destination="日本",
        depart_date=depart,
        return_date=depart + timedelta(days=4),
        concerns=[FLIGHT],
    )
    a = ItineraryActivity(
        comparison_id=c.id,
        date=depart + timedelta(days=2),
        hour=9,
        duration=2,
        category="explore",
        title="苗場滑雪",
    )
    text = user_prompt(c, [a])
    assert "第 3 天 09:00" in text
    assert ("颱風季出發" in text) is typhoon
