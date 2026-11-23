"""回答「還不確定」「不清楚」等短句時直接記成「不知道」，不呼叫 LLM（#592）。

整句只有不確定的說法才算（前面可以有「我、還、也」，後面可以帶「了、耶、欸、啊」）；
帶有其他資訊的句子（「不確定幾點，但延誤超過四小時」）照舊交給 LLM。
"""

import pytest
from app.agents.incident import ExtractResult

from tests.agents.incident.test_agent_v1 import (
    ALMOST_DONE,
    SERVICES,
    agent,
    fact,
    make_ctx,
)

UNSURE = ["還不確定", "不清楚", "不記得了", "忘了", "還不知道", "不確定耶"]


async def must_not_run(_text: str, _fields: list) -> ExtractResult:
    raise AssertionError("整句只有不確定的說法，不該呼叫 LLM")


@pytest.mark.parametrize("said", UNSURE)
async def test_短句回不確定_直接記成不知道_接著問下一題_不呼叫_LLM(said: str) -> None:
    ctx = make_ctx(facts={"event_type": "flight_delay", "date": "2026/11/03"})

    reply = await agent(extract=must_not_run).reply(ctx, said, "text", SERVICES)

    assert fact(reply, "place") == "不知道"
    assert reply.message.endswith("原本預定幾點起飛？")


@pytest.mark.parametrize("said", UNSURE)
async def test_是非題也一樣_回不確定直接記成不知道(said: str) -> None:
    # 最後一題是是非題（有沒有搭替代班機）：還沒答過
    assert "first_alt" not in ALMOST_DONE
    ctx = make_ctx(facts=ALMOST_DONE)

    reply = await agent(extract=must_not_run).reply(ctx, said, "text", SERVICES)

    assert fact(reply, "first_alt") == "不知道"


async def test_帶有其他資訊的句子不直接記成不知道_交給_LLM() -> None:
    ctx = make_ctx(facts={"event_type": "flight_delay", "date": "2026/11/03"})
    asked: list[str] = []

    async def extract(text: str, _fields: list) -> ExtractResult:
        asked.append(text)
        return ExtractResult()

    reply = await agent(extract=extract).reply(
        ctx, "不確定幾點，但延誤超過四小時", "text", SERVICES
    )

    assert asked  # 照舊交給 LLM
    assert fact(reply, "place") != "不知道"
