"""保單承保內容表：用險種代碼對照方案，讀保額與保險期間（ADR-0013）。"""

from datetime import date

from app.domain.plans import get_plan, load_plans
from app.policy.table import (
    load_code_map,
    match_plan_by_codes,
    parse_policy_facts,
    plan_codes,
)

from tests.policy.support import ADVANCED_CODES, BASIC_CODES, fubon_policy_text

CODE_MAP = load_code_map()


def test_code_map_points_into_the_clause_library(catalog) -> None:
    """對照表的每一筆都要指到條款庫真的有的險種與章。"""
    specs, rules = catalog
    sections: dict[str, set[str]] = {}
    for r in rules:
        sections.setdefault(r.insurance_code, set()).add(r.section or "")
    assert {s.code for s in specs} >= {t.insurance for t in CODE_MAP.values()}
    for code, target in CODE_MAP.items():
        if target.section is not None:
            assert target.section in sections[target.insurance], code


def test_facts_come_from_the_table_not_from_the_attached_terms() -> None:
    facts = parse_policy_facts(fubon_policy_text(ADVANCED_CODES))
    assert facts.codes == tuple(ADVANCED_CODES)  # T112、TA13 在附的條款裡，不算
    assert facts.sum_insured == 6_000_000
    assert (facts.start_date, facts.end_date) == (date(2026, 5, 28), date(2026, 6, 5))


def test_text_without_a_table_has_no_codes_and_no_amount() -> None:
    facts = parse_policy_facts("隨便一份文件 T068 TA46")
    assert facts.codes == () and facts.sum_insured is None
    assert facts.start_date is None and facts.end_date is None


def test_invalid_period_is_ignored() -> None:
    text = "自民國115年02月30日9時0分起至民國115年06月05日9時0分止"
    assert parse_policy_facts(text).start_date is None
    reversed_ = "自民國115年06月05日9時0分起至民國115年05月28日9時0分止"
    assert parse_policy_facts(reversed_).end_date is None


def test_advanced_policy_matches_the_advanced_plan_not_the_basic_one() -> None:
    """富邦基礎型與進階型含的險種代碼一樣，只差甲型的幾章：要靠代碼才分得出來。"""
    result = match_plan_by_codes(tuple(ADVANCED_CODES), load_plans(), CODE_MAP)
    assert result and result.plan.id == "fubon_advanced"
    assert result.addons == ()


def test_basic_policy_matches_the_basic_plan() -> None:
    result = match_plan_by_codes(tuple(BASIC_CODES), load_plans(), CODE_MAP)
    assert result and result.plan.id == "fubon_basic"


def test_extra_code_becomes_an_addon_with_its_chapter() -> None:
    result = match_plan_by_codes((*BASIC_CODES, "TA30"), load_plans(), CODE_MAP)
    assert result and result.plan.id == "fubon_basic"
    assert [(a.insurance, a.section) for a in result.addons] == [
        ("FUBON04", "第四章 居家火災臨時住宿補償保險")
    ]


def test_advanced_codes_plus_ticket_cancellation_match_the_official_c_plan() -> None:
    """20 個代碼整組都在官網 C 方案（加購）裡：代碼最多者勝出，沒有加購（ADR-0026）。"""
    result = match_plan_by_codes((*ADVANCED_CODES, "TA35"), load_plans(), CODE_MAP)
    assert result and result.plan.id == "fubon_official_c_addon"
    assert result.addons == ()


def test_policy_missing_a_plan_code_matches_nothing() -> None:
    assert match_plan_by_codes(tuple(BASIC_CODES[:-1]), load_plans(), CODE_MAP) is None


def test_unknown_codes_only_means_no_code_match() -> None:
    assert match_plan_by_codes(("T999", "TA99"), load_plans(), CODE_MAP) is None


def test_advanced_plan_has_the_four_extra_codes() -> None:
    basic, advanced = get_plan("fubon_basic"), get_plan("fubon_advanced")
    assert basic and advanced
    assert plan_codes(advanced, CODE_MAP) - plan_codes(basic, CODE_MAP) == {
        "TA30",
        "TA31",
        "TA33",
        "TA34",
    }


# 安達網路投保的電子保單：沒有險種代碼表，保險期間寫成「自05-Jul-2026 00時00分至…止」
CHUBB_TEXT = """旅行平安綜合保險保險單
保單號碼：ECITWAA0000153
要保人： 陳怡君
保險期間：自05-Jul-2026 00時00分至12-Jul-2026 24時00分止， 共8天
被保險人： 陳怡君 等1人
旅遊地區：泰國
總保險費：NT$412
商品名稱
保障內容
保險金額(NT$)
安達產物旅行平安保險
身故保險金或喪葬費用保險金、失能保險金
2,000,000元
重大燒燙傷保險金
傷害醫療保險金
200,000元
"""


def test_chubb_policy_period_destination_and_amount_are_read() -> None:
    from datetime import datetime

    from app.policy.table import TAIPEI

    facts = parse_policy_facts(CHUBB_TEXT)
    assert facts.start_date == date(2026, 7, 5)
    assert facts.end_date == date(2026, 7, 12)
    assert facts.start_at == datetime(2026, 7, 5, 0, 0, tzinfo=TAIPEI)
    # 「24時00分」是當天結束：記成當天 23:59，畫面才不會顯示成「7/12 00:00」
    assert facts.end_at == datetime(2026, 7, 12, 23, 59, tzinfo=TAIPEI)
    assert facts.destination == "泰國"
    assert facts.sum_insured == 2_000_000  # 身故及失能的保額，不是傷害醫療的 200,000


def test_chubb_policy_with_hours_in_the_middle_of_the_day() -> None:
    facts = parse_policy_facts(
        "保險期間：自10-Nov-2026 09時00分至11-Nov-2026 09時00分止， 共1天\n"
        "旅遊地區：日本"
    )
    assert (facts.start_date, facts.end_date) == (
        date(2026, 11, 10),
        date(2026, 11, 11),
    )
    assert facts.start_at is not None and facts.start_at.hour == 9
    assert facts.destination == "日本"


def test_chubb_policy_sample_is_read() -> None:
    from pathlib import Path

    from app.policy.extract import PdfTextExtractor

    sample = Path(__file__).parents[2] / "app/agents/document/samples/med_policy.pdf"
    text = "\n".join(p.text for p in PdfTextExtractor().extract(sample.read_bytes()))
    facts = parse_policy_facts(text)
    assert (facts.start_date, facts.end_date) == (date(2026, 7, 5), date(2026, 7, 12))
    assert facts.destination == "泰國"
    assert facts.sum_insured == 2_000_000
