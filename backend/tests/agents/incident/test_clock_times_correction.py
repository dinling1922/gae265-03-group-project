"""結論後旅客直接給原定／實際起飛時間：由程式取出並當成欄位更正，重新下結論（#382）。"""

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

# 正式站 11/15 的重現：原定 14:00、實際 22:00（同一天），延誤 8 小時，可申請
CONCLUDED_8_HOURS = {
    "event_type": "flight_delay",
    "date": "2026/11/12",
    "place": "成田機場",
    "dep_plan": "14:00",
    "dep_act": "22:00",
    "dep_act_date": "2026/11/12",
    "reason": "天候",
    "first_alt": "是",
}


async def guard_fails(_text: str) -> str:
    raise AssertionError("程式已取得時間，不該走到守門")


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("原訂14:00，實際16:00", {"dep_plan": "14:00", "dep_act": "16:00"}),
        ("原定 9:05 實際 11:30", {"dep_plan": "09:05", "dep_act": "11:30"}),
        ("實際 16:00", {"dep_act": "16:00"}),
        ("預定14：00，實際起飛16：00", {"dep_plan": "14:00", "dep_act": "16:00"}),
    ],
)
def test_取時間_有線索才取(said: str, expected: dict[str, str]) -> None:
    assert stated_clock_times(said) == expected


@pytest.mark.parametrize(
    "said",
    [
        "原定 14:00 實際 16:00 可以賠嗎？",  # 問句走條款問答
        "16:00",  # 沒有線索
        "實際 25:00",  # 不是時間
        "",
    ],
)
def test_取時間_問句與沒有線索不算(said: str) -> None:
    assert stated_clock_times(said) == {}


async def test_結論後給兩個時間_模型回空也直接更正_不問守門() -> None:
    todo = doc_todo("D03", None)
    ctx = make_ctx(facts=CONCLUDED_8_HOURS, type_code="flight_delay")
    ctx = ctx.model_copy(update={"todos": [todo]})

    reply = await agent(revise=revising(), gate=guard_fails).reply(
        ctx, "原訂14:00，實際16:00", "text", SERVICES
    )

    assert fact(reply, "dep_act") == "16:00"
    assert conclusion_of(reply) == "excluded"
    assert [(u.todo_id, u.applicable) for u in reply.todo_applicability] == [
        (todo.id, False)
    ]


async def test_只說實際時間_只改實際起飛時間() -> None:
    ctx = make_ctx(facts=CONCLUDED_8_HOURS, type_code="flight_delay")

    reply = await agent(revise=revising(), gate=guard_fails).reply(
        ctx, "實際 16:00", "text", SERVICES
    )

    assert {f.fact_key for f in reply.facts} == {"dep_act"}
    assert fact(reply, "dep_act") == "16:00"


async def test_實際日期是不知道_預設為事故當天() -> None:
    facts = {**CONCLUDED_8_HOURS, "dep_act_date": "不知道"}
    ctx = make_ctx(facts=facts, type_code="flight_delay")

    reply = await agent(revise=revising()).reply(ctx, "實際 16:00", "text", SERVICES)

    assert fact(reply, "dep_act_date") == "2026/11/12"
    assert conclusion_of(reply) == "excluded"


async def test_實際日期是不知道_說隔天照現有程式處理() -> None:
    facts = {**CONCLUDED_8_HOURS, "dep_act_date": "不知道"}
    ctx = make_ctx(facts=facts, type_code="flight_delay")

    reply = await agent(revise=revising()).reply(
        ctx, "實際隔天 02:00", "text", SERVICES
    )

    assert fact(reply, "dep_act") == "02:00"
    assert fact(reply, "dep_act_date") == "2026/11/13"


async def test_問句走條款問答_狀況不變() -> None:
    async def answer(_q, _ctx, _services):
        return QAResult("依條款說明。")

    ctx = make_ctx(facts=CONCLUDED_8_HOURS, type_code="flight_delay")

    reply = await agent(answer=answer, revise=revising()).reply(
        ctx, "原定 14:00 實際 16:00 可以賠嗎？", "text", SERVICES
    )

    assert reply.message == "依條款說明。"
    assert reply.facts == [] and reply.clauses is None


async def test_行李事件給時間_不改任何狀況() -> None:
    async def answer(_q, _ctx, _services):
        return QAResult("依條款說明。")

    ctx = concluded_baggage(THEFT_FACTS, doc_todo("D01", None))

    reply = await agent(answer=answer, revise=revising()).reply(
        ctx, "原訂14:00，實際16:00", "text", SERVICES
    )

    assert reply.facts == []
