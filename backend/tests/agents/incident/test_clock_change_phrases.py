"""「改成／延到 22:00」這類沒有「實際」字樣的說法，也記成實際起飛時間（#433）。

實際時間與日期都答過「不知道」之後，旅客在問答補上新的起飛時間：
- 說法有「改為／改成／改到／延到／延至／延後到／延後至」：程式直接取出，日期預設事故當天。
- 其他說法交給模型取出：實際日期缺或是「不知道」時，一樣預設事故當天。
"""

import pytest
from app.agents.incident import QAResult
from app.agents.incident.judge import stated_clock_times

from tests.agents.incident.test_agent_v1 import (
    SERVICES,
    THEFT_FACTS,
    agent,
    concluded_baggage,
    conclusion_of,
    doc_todo,
    fact,
    make_ctx,
    revising,
)
from tests.agents.incident.test_clock_times_correction import guard_fails

# 原定 14:00，實際時間、日期、替代班機都答「不知道」：資訊不足
UNKNOWN_ACTUAL = {
    "event_type": "flight_delay",
    "date": "2026/11/12",
    "place": "成田機場",
    "dep_plan": "14:00",
    "dep_act": "不知道",
    "dep_act_date": "不知道",
    "reason": "天候",
    "first_alt": "不知道",
}


async def model_must_not_run(_text: str, _fields: list):
    raise AssertionError("程式已取出時間，不該呼叫模型")


@pytest.mark.parametrize(
    "said",
    [
        "班機被改為22:00 起飛",
        "改成 22:00 起飛",
        "改到22:00",
        "延到 22:00 才飛",
        "延至 22:00",
        "延後到 22:00",
        "延後至 22：00",
    ],
)
def test_改成延到後面接時間_記成實際起飛時間(said: str) -> None:
    assert stated_clock_times(said) == {"dep_act": "22:00"}


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("原訂 14:00，後來改成 22:00", {"dep_plan": "14:00", "dep_act": "22:00"}),
        ("實際 16:00，後來又改成 22:00", {"dep_act": "16:00"}),  # 有「實際」以它為準
    ],
)
def test_同一句有多個時間線索_各取各的(said: str, expected: dict[str, str]) -> None:
    assert stated_clock_times(said) == expected


@pytest.mark.parametrize(
    "said",
    ["我講錯了，原定改成13:00", "原定時間改為 13:00 起飛"],
)
def test_原定加改成指同一個時間_只記原定_不記實際(said: str) -> None:
    assert stated_clock_times(said) == {"dep_plan": "13:00"}


async def test_更正原定時間_原定改變_實際不動_結論仍可申請() -> None:
    concluded = {
        **UNKNOWN_ACTUAL,
        "dep_act": "22:00",
        "dep_act_date": "2026/11/12",
        "first_alt": "是",
    }
    ctx = make_ctx(facts=concluded, type_code="flight_delay")

    reply = await agent(revise=model_must_not_run, gate=guard_fails).reply(
        ctx, "我講錯了，原定改成13:00", "text", SERVICES
    )

    assert {f.fact_key for f in reply.facts} == {"dep_plan"}
    assert fact(reply, "dep_plan") == "13:00"
    assert fact(reply, "dep_act") is None  # 仍是 22:00
    assert conclusion_of(reply) == "applicable"


@pytest.mark.parametrize(
    "said",
    [
        "改成 22:00 起飛可以賠嗎？",  # 問句走條款問答
        "航空公司說預計 20:00 起飛",  # 預計不加（#394）
        "改成晚上十點起飛",  # 沒有 HH:MM
    ],
)
def test_改成但不是時間陳述_不取(said: str) -> None:
    assert stated_clock_times(said) == {}


@pytest.mark.parametrize(
    "said",
    ["班機被改為22:00 起飛", "改成 22:00 起飛", "延到 22:00 才飛"],
)
async def test_改成延到_由程式取出_日期預設事故當天_結論可申請(said: str) -> None:
    ctx = make_ctx(facts=UNKNOWN_ACTUAL, type_code="flight_delay")

    reply = await agent(revise=model_must_not_run, gate=guard_fails).reply(
        ctx, said, "text", SERVICES
    )

    assert fact(reply, "dep_act") == "22:00"
    assert fact(reply, "dep_act_date") == "2026/11/12"
    assert fact(reply, "dep_plan") is None  # 原定不變
    assert conclusion_of(reply) == "applicable"


async def test_模型取出實際時間_實際日期不知道_預設事故當天_重新下結論() -> None:
    ctx = make_ctx(facts=UNKNOWN_ACTUAL, type_code="flight_delay")

    reply = await agent(revise=revising(dep_act="22:00")).reply(
        ctx, "後來好像是晚上十點才飛", "text", SERVICES
    )

    assert fact(reply, "dep_act") == "22:00"
    assert fact(reply, "dep_act_date") == "2026/11/12"
    assert conclusion_of(reply) == "applicable"


async def test_模型取出實際時間_已有實際日期_不被預設值覆蓋() -> None:
    known = {**UNKNOWN_ACTUAL, "dep_act": "16:00", "dep_act_date": "2026/11/13"}
    ctx = make_ctx(facts=known, type_code="flight_delay")

    reply = await agent(revise=revising(dep_act="22:00")).reply(
        ctx, "後來好像是晚上十點才飛", "text", SERVICES
    )

    assert fact(reply, "dep_act") == "22:00"
    assert fact(reply, "dep_act_date") is None  # 沒有更動，仍是 2026/11/13


async def test_模型連日期一起取出_用模型取出的日期_不被預設值覆蓋() -> None:
    ctx = make_ctx(facts=UNKNOWN_ACTUAL, type_code="flight_delay")

    reply = await agent(
        revise=revising(dep_act="22:00", dep_act_date="2026/11/13")
    ).reply(ctx, "後來是隔天晚上十點才飛", "text", SERVICES)

    assert fact(reply, "dep_act_date") == "2026/11/13"


async def test_行李事件說改成時間_不改任何狀況() -> None:
    async def answer(_q, _ctx, _services):
        return QAResult("依條款說明。")

    ctx = concluded_baggage(THEFT_FACTS, doc_todo("D01", None))

    reply = await agent(answer=answer, revise=revising()).reply(
        ctx, "改成 22:00 起飛", "text", SERVICES
    )

    assert reply.facts == []
