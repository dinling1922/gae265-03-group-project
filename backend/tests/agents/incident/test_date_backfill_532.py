"""就醫、行李：發生日期答「不知道」，之後旅客在問答說出日期時回填事故日（#532）。

班機延誤的回填規則在 test_date_backfill.py（#451）。這裡是其他事故類型：旅客這句話有明確月日、
落在保險期間內，程式就回填事故日，不靠 LLM（luna 的 revise 常回空的 answers）。
"""

from datetime import date

import pytest
from app.contracts import IncidentContext

from tests.agents.incident.test_agent_v1 import (
    QA_REPLY,
    SERVICES,
    agent,
    answers_qa,
    baggage_ctx,
    conclusion_of,
    extractor,
    fact,
    revising,
)
from tests.agents.incident.test_date_backfill import qa_fails
from tests.agents.incident.test_demo_script import medical_ctx

START, END = date(2026, 7, 5), date(2026, 7, 11)

# 其他欄位都答完、日期記「不知道」：結論資訊不足，理由是請旅客告訴我哪一天發生
MEDICAL_DATE_UNKNOWN = {
    "event_type": "medical",
    "date": "不知道",
    "place": "新宿",
    "kind": "生病",
    "hosp": "否",
    "paid": "是",
}
BAGGAGE_DATE_UNKNOWN = {
    "event_type": "baggage_loss",
    "date": "不知道",
    "place": "成田機場",
    "cause": "被偷、被搶或被扒",
    "police": "是",
    "what": "衣服",
    "compensated": "沒有",
}


def medical_unknown_date() -> IncidentContext:
    """就醫事故已有結論（問答階段），保險期間 2026-07-05～07-11。"""
    ctx = medical_ctx(facts=MEDICAL_DATE_UNKNOWN, done=True)
    policy = ctx.policy.model_copy(update={"start_date": START, "end_date": END})
    return ctx.model_copy(update={"policy": policy})


def baggage_unknown_date() -> IncidentContext:
    ctx = baggage_ctx(facts=BAGGAGE_DATE_UNKNOWN, start=START, end=END)
    return ctx.model_copy(update={"incident_type_code": "baggage_loss"})


async def test_就醫日期不知道_問答說是7月8日發生_回填事故日_重新判斷不再卡在日期() -> (
    None
):
    reply = await agent(answer=qa_fails, revise=revising()).reply(
        medical_unknown_date(), "是 7/8 發生的", "text", SERVICES
    )

    assert fact(reply, "date") == "2026/07/08"
    assert conclusion_of(reply) == "applicable"
    assert reply.clauses is not None
    assert "哪一天發生" not in (reply.clauses[0].reason or "")


async def test_行李日期不知道_問答說是7月8日發生_回填事故日() -> None:
    reply = await agent(answer=qa_fails, revise=revising()).reply(
        baggage_unknown_date(), "是 7/8 發生的", "text", SERVICES
    )

    assert fact(reply, "date") == "2026/07/08"


@pytest.mark.parametrize("make", [medical_unknown_date, baggage_unknown_date])
async def test_日期不知道_問答問的日期在保險期間外_不回填_照常回答(make) -> None:
    # 「11/20 前要送件嗎」的 11/20 是期限，不是事故日
    reply = await agent(answer=answers_qa, revise=revising()).reply(
        make(), "11/20 前要送件嗎", "text", SERVICES
    )

    assert reply.message == QA_REPLY
    assert fact(reply, "date") is None


async def test_就醫追問中_日期先答不知道_答別題時順帶說出日期_也回填() -> None:
    facts = {k: v for k, v in MEDICAL_DATE_UNKNOWN.items() if k not in ("hosp", "paid")}
    ctx = medical_ctx(facts=facts)
    policy = ctx.policy.model_copy(update={"start_date": START, "end_date": END})
    ctx = ctx.model_copy(update={"policy": policy})

    reply = await agent(extract=extractor(event_type="medical", hosp="否")).reply(
        ctx, "沒有住院，7/8 看的醫生", "text", SERVICES
    )

    assert fact(reply, "hosp") == "否"
    assert fact(reply, "date") == "2026/07/08"
