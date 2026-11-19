"""就醫依險種分開判斷：傷害醫療 CHUBB02、海外突發疾病 CHUBB04（#546）。

CHUBB02 第 1 條（意外傷害事故發生日起一百八十日內就醫，依收據理賠）、第 3 條（申領文件）；
意外傷害事故定義在 CHUBB01 第 2 條，除外責任在第 9 條。CHUBB04 只承保疾病。
"""

from datetime import date

import pytest
from app.agents.incident.judge import judge_medical

START, END = date(2026, 7, 5), date(2026, 7, 11)
INJURY_CODE, ILLNESS_CODE = "CHUBB02", "CHUBB04"
ASK_DATE = "請告訴我事故是哪一天發生的，才能確認是否在保險期間內"
BASE = {"date": "2026/07/08", "hosp": "否"}


def judged(code: str, **known: str):
    return judge_medical({**BASE, **known}, START, END, code)


def test_受傷_傷害醫療可能適用_理由寫一百八十日與依收據理賠_不寫金額() -> None:
    result = judged(INJURY_CODE, kind="受傷")

    assert result.conclusion == "applicable"
    assert "一百八十日" in result.reason
    assert "依收據在保單額度內理賠" in result.reason
    assert not any(ch.isdigit() for ch in result.reason)  # 數字不由程式以外的地方產生
    # 除外責任（CHUBB01 第 9 條）與「必要時要意外傷害事故證明」用提醒句帶過
    assert "除外責任" in result.reason
    assert "意外傷害事故證明" in result.reason


def test_受傷_突發疾病不適用_說明受傷由傷害醫療處理() -> None:
    result = judged(ILLNESS_CODE, kind="受傷")

    assert result.conclusion == "excluded"
    assert "不屬於" in result.reason
    assert "傷害醫療" in result.reason


def test_生病_突發疾病可能適用_理由照舊() -> None:
    result = judged(ILLNESS_CODE, kind="生病")

    assert result.conclusion == "applicable"
    assert result.reason.startswith(
        "海外突發疾病門診可申請醫療保險金，依收據在保單額度內理賠"
    )
    assert "出發前九十日內曾就同一疾病就診" in result.reason


def test_生病_傷害醫療不適用_說明生病由突發疾病處理() -> None:
    result = judged(INJURY_CODE, kind="生病")

    assert result.conclusion == "excluded"
    assert "不屬於" in result.reason
    assert "突發疾病" in result.reason


@pytest.mark.parametrize("code", [INJURY_CODE, ILLNESS_CODE])
@pytest.mark.parametrize("kind", [None, "不知道"])
def test_還不知道生病或受傷_兩個險種都請旅客確認(code: str, kind: str | None) -> None:
    known = {} if kind is None else {"kind": kind}

    result = judged(code, **known)

    assert result.conclusion == "insufficient"
    assert result.reason == "需確認是生病還是受傷"


@pytest.mark.parametrize("code", [INJURY_CODE, ILLNESS_CODE])
@pytest.mark.parametrize("kind", ["受傷", "生病"])
def test_日期不知道_兩個險種都是請補日期(code: str, kind: str) -> None:
    result = judge_medical({"date": "不知道", "kind": kind}, START, END, code)

    assert result.conclusion == "insufficient"
    assert result.reason == ASK_DATE


@pytest.mark.parametrize("code", [INJURY_CODE, ILLNESS_CODE])
@pytest.mark.parametrize("kind", ["受傷", "生病"])
def test_日期在保險期間外_兩個險種都不在保障範圍(code: str, kind: str) -> None:
    result = judge_medical({"date": "2026/07/20", "kind": kind}, START, END, code)

    assert result.conclusion == "excluded"
    assert "不在保險期間內" in result.reason
