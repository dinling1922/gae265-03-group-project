"""合格規範的比對（rules.py）與日期解析（dates.py）。

陷阱寫法來自合格判定埋錯集（tools/agent6-qualification-eval/）：欄名後面接的數字或字，
不一定是那個欄位的值（登機時間、卡號末四碼、收據號碼、「見附件」）。
"""

import json
from datetime import date
from pathlib import Path

import pytest
from app.agents.document.dates import dates_in
from app.agents.document.rules import FieldRule, load_rules, matched_lines

SOURCE = {
    "file": "backend/data/config/incident_catalog.json",
    "quote": "需載明病名與就診日期",
}


def one_rule(tmp_path: Path, way: list[dict]) -> FieldRule:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "code": "D13",
                        "field": "x",
                        "label": "x",
                        "severity": "incomplete",
                        "any_of": [way],
                        "missing_reason": "x",
                        "source": SOURCE,
                        "verified": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return load_rules(path)[0]


def shipped(code: str, field: str) -> FieldRule:
    return next(r for r in load_rules() if r.code == code and r.field == field)


# ---- 條件的寫法 ----


def test_exclude_drops_lines_that_only_look_like_the_field(tmp_path: Path) -> None:
    rule = one_rule(tmp_path, [{"pattern": "就診日", "exclude": "次回|下次"}])
    assert matched_lines(rule, ["下次就診日：7/10"]) is None
    assert matched_lines(rule, ["就診日：7/8"]) == ["就診日：7/8"]


def test_date_after_needs_a_readable_date_after_the_label(tmp_path: Path) -> None:
    rule = one_rule(tmp_path, [{"pattern": "就診日期", "date": "after"}])
    assert matched_lines(rule, ["就診日期：＿＿＿＿　病歷號碼：0012345"]) is None
    assert matched_lines(rule, ["2026-07-08 就診日期："]) is None  # 日期在欄名前面
    assert matched_lines(rule, ["就診日期：2026-07-08"]) == ["就診日期：2026-07-08"]


def test_date_line_needs_a_readable_date_anywhere_on_the_line(tmp_path: Path) -> None:
    rule = one_rule(tmp_path, [{"pattern": "\\d", "date": "line"}])
    assert matched_lines(rule, ["診療時間 9.00-12.30"]) is None
    assert matched_lines(rule, ["2026.07.08"]) == ["2026.07.08"]


def test_next_line_reads_the_value_under_a_label_only_line(tmp_path: Path) -> None:
    rule = one_rule(
        tmp_path,
        [{"pattern": "^Diagnosis$", "next_line": "^[A-Za-z]{3}"}],
    )
    assert matched_lines(rule, ["Diagnosis", "Acute gastroenteritis"]) == [
        "Diagnosis",
        "Acute gastroenteritis",
    ]
    assert matched_lines(rule, ["Diagnosis", "＿＿＿"]) is None
    assert matched_lines(rule, ["Diagnosis"]) is None  # 最後一行，沒有下一行


def test_unknown_date_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        one_rule(tmp_path, [{"pattern": "x", "date": "sometimes"}])


# ---- 日期解析 ----


def test_a_four_digit_group_with_a_leading_zero_is_not_a_year() -> None:
    # 收據號碼 R-26-07-0153 不是 2064 年（民國 153 年）
    assert dates_in("領収書番号：R-26-07-0153") == set()
    assert dates_in("08/07/2026") == {date(2026, 7, 8), date(2026, 8, 7)}
    assert dates_in("2026-07-08") == {date(2026, 7, 8)}


# ---- 出貨的規則：寫法與陷阱 ----


@pytest.mark.parametrize(
    "lines",
    [
        ["原定起飛時間：10:30", "實際起飛時間：16:45"],
        ["The above flight was delayed by 6.5 hours."],
        ["延誤時間：約 6 小時"],
        ["延誤時間：6 小時 15 分"],
        ["遅延時間：約6時間"],
        ["Length of delay: 375 minutes"],
        ["定刻出発時刻：10:30", "実際出発時刻：16:45"],
        ["原定起飛：2026-07-10 10:30", "上述班機因颱風取消，特此證明。"],
    ],
)
def test_delay_period_is_found(lines: list[str]) -> None:
    assert matched_lines(shipped("D01", "delay_period"), lines) is not None


@pytest.mark.parametrize(
    "lines",
    [
        ["登機時間：10:00", "原定起飛時間：10:30"],  # 已知風險：兩個時間都不是實際起飛
        ["Scheduled departure (STD): 10:30", "Issued at 18:20, NRT Airport Station"],
        ["註：延誤證明須於班機抵達後 24 小時內向櫃檯申請。"],
        [
            "Scheduled departure: 10:30",
            "To cancel or change your booking, please visit our website.",
        ],
        ["證明編號 Certificate No.：NRT-DLY-20260710-0412"],
    ],
)
def test_delay_period_traps_are_not_found(lines: list[str]) -> None:
    assert matched_lines(shipped("D01", "delay_period"), lines) is None


@pytest.mark.parametrize(
    "lines",
    [
        ["病名：急性腸胃炎"],
        ["Diagnosis: Acute gastroenteritis"],
        ["傷病名：急性胃腸炎"],
        ["診断名：急性胃腸炎"],
        ["病名：急性胃腸炎（Acute gastroenteritis）"],
        ["Diagnosis", "Acute gastroenteritis"],
    ],
)
def test_diagnosis_is_found(lines: list[str]) -> None:
    assert matched_lines(shipped("D13", "diagnosis"), lines) is not None


@pytest.mark.parametrize(
    "lines",
    [
        ["診斷：＿＿＿＿＿＿"],
        ["Diagnosis: see attached"],
        ["診断：検査結果待ち"],
        ["診斷："],
        ["Diagnosis: N/A"],
        ["病名 / Diagnosis", "症状 / Symptoms"],  # demo 診斷書拿掉病名後的表格欄名
        ["Diagnosis", "Treatment: IV fluids"],  # 下一行是別的欄位
    ],
)
def test_diagnosis_placeholders_are_not_found(lines: list[str]) -> None:
    assert matched_lines(shipped("D13", "diagnosis"), lines) is None


@pytest.mark.parametrize(
    "line",
    [
        "就診日期：115年7月8日",
        "The patient was seen at our clinic on 8 July 2026.",
        "受診日：令和8年7月8日",
        "診察日：2026年7月8日",
        "受診日：2026年7月8日（水）",
        "Date of visit: 8 Jul 2026",
    ],
)
def test_visit_date_is_found(line: str) -> None:
    assert matched_lines(shipped("D13", "visit_date"), [line]) is not None


@pytest.mark.parametrize(
    "line",
    [
        "The patient was examined at Tokyo Demo International Clinic, 1-2-3 Demo-cho, Chiyoda-ku.",
        "次回受診日：2026年7月10日",
        "就診日期：＿＿＿＿　病歷號碼：0012345",
        "受診日 / Date of visit",
        "開立日期：2026/07/09",
    ],
)
def test_visit_date_traps_are_not_found(line: str) -> None:
    assert matched_lines(shipped("D13", "visit_date"), [line]) is None


@pytest.mark.parametrize(
    "line",
    [
        "實收金額：10,640 日圓",
        "Total amount: JPY 4,440",
        "合計 10,640円",
        "ご請求額　￥10,640－",
        "合計 Total：¥10,640",
    ],
)
def test_amount_is_found(line: str) -> None:
    assert matched_lines(shipped("D14", "amount"), [line]) is not None


@pytest.mark.parametrize(
    "line",
    [
        "Paid by credit card ****1234",
        "合計項目：4 項",
        "統一發票號碼：AB-12345678",
        "電話：03-0000-0000",
        "上記の金額を2026年7月8日に領収いたしました。",
        "金額（円）",
        "領収書番号：R-20260708-0153",
    ],
)
def test_amount_traps_are_not_found(line: str) -> None:
    assert matched_lines(shipped("D14", "amount"), [line]) is None


@pytest.mark.parametrize(
    "line",
    [
        "日期：2026-07-08",
        "Date: 8 Jul 2026",
        "診療日：2026年7月8日",
        "発行日：令和8年7月8日",
        "Date: July 8, 2026",
        "2026.07.08",
    ],
)
def test_receipt_date_is_found(line: str) -> None:
    assert matched_lines(shipped("D14", "date"), [line]) is not None


@pytest.mark.parametrize(
    "line",
    [
        "領収書番号：R-26-07-0153",
        "診療時間 9.00-12.30 / 14.00-18.00",
        "出生日期：1992-03-14",
        "TEL 03-0000-0000",
        "生年月日 / Date of birth",
        "1992年3月14日生（34歳）",
    ],
)
def test_receipt_date_traps_are_not_found(line: str) -> None:
    assert matched_lines(shipped("D14", "date"), [line]) is None
