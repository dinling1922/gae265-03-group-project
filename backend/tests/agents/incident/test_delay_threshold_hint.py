"""班機延誤算不出延誤時，理由附上 4 小時門檻（#533）。

數字由 `THRESHOLD_HOURS` 組出，不寫死、不由 LLM 產生（ADR-0004）。只有資訊不足的那句加；
可能適用、未滿門檻的理由本來就有門檻，其他事故類型的理由也不帶。
"""

from datetime import date

import pytest
from app.agents.incident.judge import (
    THRESHOLD_HOURS,
    judge_baggage_loss,
    judge_flight_delay,
    judge_medical,
)

FULL = {
    "date": "2026/11/03",
    "dep_plan": "14:00",
    "dep_act": "22:00",
    "dep_act_date": "2026/11/03",
    "first_alt": "是",
}
HINT = f"延誤 {THRESHOLD_HOURS} 小時以上才可申請理賠"


@pytest.mark.parametrize(
    ("known", "missing"),
    [
        ({**FULL, "dep_act": "不知道"}, "實際起飛時間"),
        ({k: v for k, v in FULL.items() if k != "dep_act_date"}, "實際起飛日期"),
    ],
    ids=["缺實際起飛時間", "缺實際起飛日期"],
)
def test_算不出延誤_理由列出缺的項目_並附門檻(
    known: dict[str, str], missing: str
) -> None:
    judged = judge_flight_delay(known)

    assert judged.conclusion == "insufficient"
    assert judged.reason == f"還需要{missing}，才能算出延誤多久；{HINT}"


def test_可能適用與未滿門檻的理由不變() -> None:
    assert judge_flight_delay(FULL).reason == "延誤四小時以上可申請班機延誤保險金"
    assert judge_flight_delay({**FULL, "dep_act": "16:00"}).reason == (
        "延誤未滿四小時，依條款不在保障範圍；如有疑問請向保險公司確認"
    )


@pytest.mark.parametrize(
    "judged",
    [
        judge_medical({"date": "2026/11/03"}, date(2026, 11, 1), date(2026, 11, 8)),
        judge_medical({}, date(2026, 11, 1), date(2026, 11, 8)),
        judge_baggage_loss(
            {"date": "2026/11/03"}, date(2026, 11, 1), date(2026, 11, 8)
        ),
        judge_baggage_loss({}, date(2026, 11, 1), date(2026, 11, 8)),
    ],
    ids=["就醫缺生病或受傷", "就醫缺日期", "行李缺原因", "行李缺日期"],
)
def test_就醫與行李的資訊不足理由不帶班機門檻(judged) -> None:
    assert judged.conclusion == "insufficient"
    assert "小時以上才可申請理賠" not in judged.reason
