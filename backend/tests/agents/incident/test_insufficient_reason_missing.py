"""班機延誤算不出延誤時，理由要指出實際還缺哪一項，不是籠統一句（#431）。"""

from app.agents.incident.judge import THRESHOLD_HOURS, judge_flight_delay

FULL = {
    "date": "2026/11/03",
    "dep_plan": "14:00",
    "dep_act": "22:00",
    "dep_act_date": "2026/11/03",
}


def without(*keys: str, **unknown: str) -> dict[str, str]:
    known = {k: v for k, v in FULL.items() if k not in keys}
    return {**known, **unknown}


def test_只缺原定起飛時間_理由提到原定_不提實際() -> None:
    result = judge_flight_delay(without(dep_plan="不知道"))

    assert result.conclusion == "insufficient"
    assert "原定起飛時間" in result.reason
    assert "實際" not in result.reason


def test_只缺實際起飛時間_理由提到實際起飛時間() -> None:
    result = judge_flight_delay(without(dep_act="不知道"))
    assert "實際起飛時間" in result.reason
    assert "原定" not in result.reason
    assert "實際起飛日期" not in result.reason


def test_只缺實際起飛日期_理由提到實際起飛日期() -> None:
    result = judge_flight_delay(without("dep_act_date"))

    assert result.conclusion == "insufficient"
    assert "實際起飛日期" in result.reason
    assert "原定" not in result.reason and "實際起飛時間" not in result.reason


def test_缺多項_都列出() -> None:
    result = judge_flight_delay({"date": "2026/11/03", "dep_plan": "不知道"})

    assert result.conclusion == "insufficient"
    for item in ("原定起飛時間", "實際起飛時間", "實際起飛日期"):
        assert item in result.reason


def test_理由是一句完整的話() -> None:
    result = judge_flight_delay(without(dep_plan="不知道"))

    assert result.reason == (
        f"還需要原定起飛時間，才能算出延誤多久；延誤 {THRESHOLD_HOURS} 小時以上才可申請理賠"
    )
