"""推薦方案與對照方案的選法（spec #457、ADR-0032）：用小型假報價測規則。"""

from app.domain.benefits import Concern
from app.domain.recommendation import Benefit, ConcernLevel, Quotes, pick

FLIGHT, MEDICAL, CARD = Concern.FLIGHT_DELAY, Concern.MEDICAL, Concern.CARD_FRAUD
NAMES = {
    FLIGHT: "班機延誤保險(定額給付-累進式)",
    MEDICAL: "海外突發疾病住院醫療",
    CARD: "信用卡盜刷損失保險",
}
M = 1_000_000


def quotes(rows: dict[tuple[str, int], tuple[int, dict[Concern, int]]]) -> Quotes:
    premiums: dict[str, dict[int, int]] = {}
    benefits = {}
    for (code, sum_insured), (premium, amounts) in rows.items():
        premiums.setdefault(code, {})[sum_insured] = premium
        benefits[(code, sum_insured)] = [
            Benefit(NAMES[c], None, a) for c, a in amounts.items()
        ]
    return Quotes(premiums, benefits)


def high(c: Concern) -> ConcernLevel:
    return ConcernLevel(concern=c, level="high")


def enough(c: Concern) -> ConcernLevel:
    return ConcernLevel(concern=c, level="enough")


def test_enough_keeps_half_of_the_max_then_takes_the_cheapest() -> None:
    q = quotes(
        {
            ("taian", 2 * M): (100, {FLIGHT: 400}),
            ("chubb_basic", 2 * M): (200, {FLIGHT: 1000}),
            ("huanan_basic", 3 * M): (300, {FLIGHT: 2000}),
        }
    )
    # 門檻 1000：泰安不夠；剩下取最便宜的安達。對照方案在更便宜的泰安裡選
    assert pick(q, [enough(FLIGHT)]) == (("chubb_basic", 2 * M), ("taian", 2 * M))


def test_enough_is_skipped_when_no_remaining_pair_has_it() -> None:
    q = quotes(
        {
            ("taian", 2 * M): (100, {FLIGHT: 0}),
            ("chubb_basic", 2 * M): (200, {FLIGHT: 0}),
        }
    )
    assert pick(q, [enough(FLIGHT)]) == (("taian", 2 * M), ("chubb_basic", 2 * M))


def test_high_keeps_eighty_percent_of_the_max_including_higher_sums() -> None:
    # 醫療額度隨保額提高：華南 300 萬只有 600、500 萬 900（≥ 1000 × 0.8），比富邦 1000 便宜
    q = quotes(
        {
            ("huanan_basic", 3 * M): (300, {MEDICAL: 600}),
            ("huanan_basic", 5 * M): (500, {MEDICAL: 900}),
            ("fubon_advanced", 6 * M): (900, {MEDICAL: 1000}),
        }
    )
    assert pick(q, [high(MEDICAL)]) == (
        ("huanan_basic", 5 * M),
        ("fubon_advanced", 6 * M),  # 推薦已最便宜的方案：在其他方案裡選
    )


def test_enough_filters_run_before_high_in_concern_order() -> None:
    # 越高越好排第 1，但「有就好」先篩：泰安盜刷不夠被刷掉，醫療最高的就不是它
    q = quotes(
        {
            ("taian", 2 * M): (100, {MEDICAL: 1000, CARD: 0}),
            ("chubb_basic", 2 * M): (200, {MEDICAL: 500, CARD: 10_000}),
            ("tfmi_basic", 2 * M): (300, {MEDICAL: 450, CARD: 10_000}),
        }
    )
    rec, alt = pick(q, [high(MEDICAL), enough(CARD)]) or ((), ())
    assert rec == ("chubb_basic", 2 * M)
    assert alt == ("taian", 2 * M)  # 比推薦便宜的只有泰安


def test_same_premium_follows_plans_order_then_lower_sum() -> None:
    q = quotes(
        {
            ("chubb_basic", 3 * M): (100, {FLIGHT: 1000}),
            ("chubb_basic", 2 * M): (100, {FLIGHT: 1000}),
            ("taian", 2 * M): (100, {FLIGHT: 1000}),
        }
    )
    assert pick(q, [enough(FLIGHT)]) == (("taian", 2 * M), ("chubb_basic", 2 * M))


def test_fewer_than_two_plans_cannot_recommend() -> None:
    q = quotes(
        {
            ("taian", 2 * M): (100, {FLIGHT: 1000}),
            ("taian", 3 * M): (150, {FLIGHT: 1000}),
        }
    )
    assert pick(q, [enough(FLIGHT)]) is None
