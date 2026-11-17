"""班機延誤：發生日期答「不知道」，之後旅客說出日期時回填事故日（#451）。

事故日 ＝ 原定起飛日。原定起飛時間那句有日期就用那個日期；沒有就用實際起飛日期（並標出是推定的）；
都沒有維持不知道。程式決定，不靠 LLM。
"""

from datetime import date

import pytest
from app.agents.incident import QAResult
from app.agents.incident.judge import (
    DATE_INFERRED_KEY,
    INFERRED_NOTE,
    backfilled_date,
    stated_clock_times,
    stated_planned_date,
)

from tests.agents.incident.test_agent_v1 import (
    SERVICES,
    agent,
    conclusion_of,
    extractor,
    fact,
    make_ctx,
    revising,
)

START, END = date(2026, 7, 5), date(2026, 7, 11)

# Leon 11/17 的實例：發生日期答「不知道」，原定答「7/11 10:30」只記了 10:30，其他都答不知道
DATE_UNKNOWN = {
    "event_type": "flight_delay",
    "date": "不知道",
    "place": "成田機場",
    "dep_plan": "10:30",
    "dep_act": "不知道",
    "dep_act_date": "不知道",
    "reason": "天候",
    "first_alt": "不知道",
}


async def qa_fails(*_args, **_kwargs) -> QAResult:
    raise AssertionError("旅客是在補充資訊，不該走到條款問答（會貼條款原文）")


def concluded(**changes: str):
    facts = {**DATE_UNKNOWN, **changes}
    return make_ctx(facts=facts, type_code="flight_delay", start=START, end=END)


# ---- 取出原定起飛日期 ----


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("原本7/11 10:30，後來實際 16:45 起飛", "2026/07/11"),
        ("原定 7月11日 10:30", "2026/07/11"),
        ("預定 2026/07/11 10:30 起飛", "2026/07/11"),
    ],
)
def test_取原定日期_原定時間那句有日期才取(said: str, expected: str) -> None:
    assert stated_planned_date(said, 2026, START, END) == expected


@pytest.mark.parametrize(
    "said",
    [
        "原定 10:30，實際 16:45",  # 沒有日期
        "7/11 16:45 起飛",  # 日期跟的是實際起飛，不是原定
        "原本7/11 10:30 的班機，延誤到幾點？",  # 問句
        "原本打算 7/11 出門",  # 後面沒有接時間
        "",
    ],
)
def test_取原定日期_沒有線索或問句不取(said: str) -> None:
    assert stated_planned_date(said, 2026, START, END) is None


def test_原本也算原定時間的線索_日期夾在中間也取得到時間() -> None:
    assert stated_clock_times("原本7/11 10:30，後來實際 16:45 起飛") == {
        "dep_plan": "10:30",
        "dep_act": "16:45",
    }
    assert stated_clock_times("原定 14:00 實際 16:00") == {
        "dep_plan": "14:00",
        "dep_act": "16:00",
    }


# ---- 回填規則 ----


def test_回填_有原定日期就用原定_不標推定() -> None:
    got = backfilled_date(
        {"date": "不知道", "dep_act_date": "2026/07/12"}, "2026/07/11"
    )
    assert got is not None
    assert (got.date, got.inferred) == ("2026/07/11", False)


def test_回填_沒有原定日期就用實際日期_標推定() -> None:
    got = backfilled_date({"date": "不知道", "dep_act_date": "2026/07/11"}, None)
    assert got is not None
    assert (got.date, got.inferred) == ("2026/07/11", True)


@pytest.mark.parametrize("current", [None, "", "不知道", "7/11"])
def test_回填_日期缺或不是明確日期才補(current: str | None) -> None:
    known = {"dep_act_date": "2026/07/11"} | (
        {} if current is None else {"date": current}
    )
    assert backfilled_date(known, None) is not None


def test_回填_已有明確日期不覆蓋() -> None:
    known = {"date": "2026/07/10", "dep_act_date": "2026/07/11"}
    assert backfilled_date(known, "2026/07/12") is None


def test_回填_兩個日期都沒有就不動() -> None:
    assert backfilled_date({"date": "不知道", "dep_act_date": "不知道"}, None) is None


# ---- 結論後補充（問答更正路徑）----


async def test_原本日期時間_後來實際起飛_回填事故日_重新下結論_不貼條款原文() -> None:
    reply = await agent(answer=qa_fails, revise=revising()).reply(
        concluded(), "原本7/11 10:30，後來實際 16:45 起飛", "text", SERVICES
    )

    assert fact(reply, "date") == "2026/07/11"
    assert fact(reply, "dep_act_date") == "2026/07/11"
    assert conclusion_of(reply) == "applicable"
    assert fact(reply, DATE_INFERRED_KEY) is None  # 用原定日期，不是推定


async def test_實際時間沒變_只有日期補上_也走更正_不落到條款問答() -> None:
    ctx = concluded(dep_act="16:45", dep_act_date="不知道")

    reply = await agent(answer=qa_fails, revise=revising()).reply(
        ctx, "原本7/11 10:30，後來實際 16:45 起飛", "text", SERVICES
    )

    assert fact(reply, "date") == "2026/07/11"
    assert conclusion_of(reply) == "applicable"


async def test_只說實際起飛日期_用它推定事故日_並標出是推定的() -> None:
    reply = await agent(
        answer=qa_fails, revise=revising(dep_act="16:45", dep_act_date="2026/07/11")
    ).reply(concluded(), "7/11 16:45 起飛", "text", SERVICES)

    assert fact(reply, "date") == "2026/07/11"
    assert fact(reply, DATE_INFERRED_KEY) == "2026/07/11"
    assert conclusion_of(reply) == "applicable"
    assert reply.clauses is not None
    assert INFERRED_NOTE in (reply.clauses[0].reason or "")


async def test_沒有任何日期_實際_16_45_維持資訊不足() -> None:
    reply = await agent(answer=qa_fails, revise=revising()).reply(
        concluded(), "實際 16:45 起飛", "text", SERVICES
    )

    assert fact(reply, "date") is None
    assert fact(reply, "dep_act") == "16:45"
    assert conclusion_of(reply) == "insufficient"


async def test_已有明確事故日_不被覆蓋() -> None:
    ctx = concluded(date="2026/07/10")

    reply = await agent(
        answer=qa_fails, revise=revising(dep_act="16:45", dep_act_date="2026/07/11")
    ).reply(ctx, "7/11 16:45 起飛", "text", SERVICES)

    assert fact(reply, "date") is None
    assert fact(reply, DATE_INFERRED_KEY) is None


async def test_回填的日期不在保險期間內_照規則判不在保障範圍() -> None:
    reply = await agent(
        answer=qa_fails, revise=revising(dep_act="16:45", dep_act_date="2026/07/20")
    ).reply(concluded(), "7/20 16:45 起飛", "text", SERVICES)

    assert fact(reply, "date") == "2026/07/20"
    assert conclusion_of(reply) == "excluded"


# ---- 追問中 ----


async def test_追問中原定答日期加時間_日期不知道也回填() -> None:
    ctx = make_ctx(
        facts={"event_type": "flight_delay", "date": "不知道", "place": "成田機場"},
        start=START,
        end=END,
    )

    reply = await agent(extract=extractor(dep_plan="10:30")).reply(
        ctx, "7/11 10:30", "text", SERVICES
    )

    assert fact(reply, "dep_plan") == "10:30"
    assert fact(reply, "date") == "2026/07/11"
    assert fact(reply, DATE_INFERRED_KEY) is None


async def test_追問中原定只答時間_沒有日期可補_維持不知道() -> None:
    ctx = make_ctx(
        facts={"event_type": "flight_delay", "date": "不知道", "place": "成田機場"},
        start=START,
        end=END,
    )

    reply = await agent(extract=extractor(dep_plan="10:30")).reply(
        ctx, "10:30", "text", SERVICES
    )

    assert fact(reply, "date") is None
