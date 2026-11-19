"""事故發生日期答「不知道」：資訊不足的理由要直接說缺什麼、怎麼補（#532）。

三種事故類型共用 `_outside_period`；日期在保險期間外仍是「不在保障範圍」。
"""

from datetime import date

import pytest
from app.agents.incident.judge import (
    judge_baggage_loss,
    judge_flight_delay,
    judge_medical,
)

START, END = date(2026, 7, 5), date(2026, 7, 11)
ASK_DATE = "請告訴我事故是哪一天發生的，才能確認是否在保險期間內"

JUDGES = {
    "就醫": (judge_medical, {"kind": "生病", "hosp": "否"}),
    "行李": (judge_baggage_loss, {"cause": "被偷、被搶或被扒", "police": "是"}),
    "班機延誤": (judge_flight_delay, {"delay_hours": "6"}),
}


@pytest.mark.parametrize("type_name", list(JUDGES))
@pytest.mark.parametrize("date_value", ["不知道", None])
def test_日期不知道_理由直接請旅客告訴我哪一天發生(
    type_name: str, date_value: str | None
) -> None:
    judge, rest = JUDGES[type_name]
    known = dict(rest) | ({} if date_value is None else {"date": date_value})

    judged = judge(known, START, END)

    assert judged.conclusion == "insufficient"
    assert judged.reason == ASK_DATE
    assert "哪一天發生" in judged.reason


@pytest.mark.parametrize("type_name", list(JUDGES))
def test_日期在保險期間外_仍是不在保障範圍(type_name: str) -> None:
    judge, rest = JUDGES[type_name]

    judged = judge({**rest, "date": "2026/07/20"}, START, END)

    assert judged.conclusion == "excluded"
    assert "不在保障範圍" in judged.reason
