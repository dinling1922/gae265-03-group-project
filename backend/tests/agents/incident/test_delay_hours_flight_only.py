"""旅客說出的「延誤幾小時」只記在班機延誤事件上（T4）。

行李或就醫的追問中順帶說「班機也延誤了 6 小時」，不能記成這個事件的 delay_hours；
結論後的更正（stated_delay_hours）同樣只對班機延誤生效。
"""

import pytest

from tests.agents.incident.test_agent_v1 import (
    QA_REPLY,
    SERVICES,
    THEFT_FACTS,
    agent,
    answers_qa,
    baggage_ctx,
    concluded_baggage,
    extractor,
    fact,
    make_ctx,
    revising,
)
from tests.agents.incident.test_demo_script import medical_ctx

MENTION = "不知道，然後因為天候因素，班機也延誤了，延誤了6個小時"
# 行李遺失追問到「業者有沒有賠償」這一題
ASKING_BAGGAGE = {
    k: v for k, v in THEFT_FACTS.items() if k not in ("compensated", "what")
}


@pytest.mark.parametrize(
    "where",
    ["追問中", "第一句描述"],
)
async def test_行李事件提到延誤時數_不記_delay_hours(where: str) -> None:
    if where == "追問中":
        reply = await agent(
            extract=extractor(event_type="baggage_loss", compensated="還不知道")
        ).reply(baggage_ctx(facts=ASKING_BAGGAGE), MENTION, "text", SERVICES)
        assert fact(reply, "compensated") == "還不知道"  # 這一題的答案照記
    else:
        reply = await agent(extract=extractor(event_type="baggage_loss")).start(
            baggage_ctx(description="我的行李在京都被偷了，班機也延誤了六小時"),
            SERVICES,
        )
        assert fact(reply, "event_type") == "baggage_loss"

    assert fact(reply, "delay_hours") is None


async def test_就醫事件提到延誤時數_不記_delay_hours() -> None:
    facts = {
        "event_type": "medical",
        "place": "新宿",
        "kind": "生病",
        "date": "2027/09/07",
    }

    reply = await agent().reply(
        medical_ctx(facts=facts), "有，不過我的班機也延誤了六小時", "text", SERVICES
    )

    assert fact(reply, "delay_hours") is None


async def test_班機延誤事件說延誤了六小時_照舊記下時數_跳過時間題() -> None:
    reply = await agent().start(make_ctx(description="班機延誤了六小時"), SERVICES)

    assert fact(reply, "delay_hours") == "6"
    assert "幾點起飛" not in reply.message


async def test_行李事件結論後說其實只延誤_2_小時_不改任何狀況() -> None:
    ctx = concluded_baggage(THEFT_FACTS)

    # 行李事件沒有「延誤時數」可以更正：這句不當成更正，照常走條款問答
    reply = await agent(answer=answers_qa, revise=revising()).reply(
        ctx, "我講錯了，其實只延誤 2 小時", "text", SERVICES
    )

    assert reply.message == QA_REPLY
    assert reply.facts == [] and reply.clauses is None
