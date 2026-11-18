"""1-3 推薦方案與比較表（spec #61、spec #457、ADR-0034）。

推薦規則：在該天數下所有「方案 × 身故保額」組合中，依需求等級先篩「基本保障」、再篩「加強保障」，
取保費最低的。對照方案（畫面上的「次推薦」）是不同方案：在比推薦便宜的組合中用同一套規則選，
推薦已是最便宜時在其他所有組合中選。依據與比較表是固定句型，數字全部由程式填入（ADR-0004）；
推薦理由由需求等級的原因加上固定句型的結論組成，不含數字。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domain.benefits import (
    CATEGORY_CONCERN,
    CONCERN_BENEFITS,
    DEATH_BENEFIT,
    DEATH_LABEL,
    EXPLANATIONS,
    EXTRA_ROW_LABELS,
    ILLNESS_CATEGORIES,
    ILLNESS_LABEL,
    BenefitCategory,
    Concern,
)
from app.domain.plans import load_plans


@dataclass(frozen=True)
class Benefit:
    name: str
    category: str | None
    amount: int


@dataclass(frozen=True)
class Quotes:
    """某天數下的保費與保障明細；只含在這個天數有報價的方案。"""

    premiums: dict[str, dict[int, int]]  # 方案 → 身故保額 → 保費
    benefits: dict[
        tuple[str, int], list[Benefit]
    ]  # (方案, 身故保額) → 明細（依原順序）

    def options(self, plan_code: str) -> list[int]:
        return sorted(self.premiums.get(plan_code, {}))

    def concern_amount(self, plan_code: str, sum_insured: int, concern: Concern) -> int:
        names = CONCERN_BENEFITS[concern]
        return sum(
            b.amount
            for b in self.benefits.get((plan_code, sum_insured), [])
            if b.name in names
        )


Level = Literal["high", "enough"]  # 加強保障、基本保障
Source = Literal["llm", "default"]  # 需求等級來自 LLM，或是預設等級


class ConcernLevel(BaseModel):
    """需求等級：某個在意事項要「加強保障」（high）還是「基本保障」（enough）。"""

    concern: Concern
    level: Level
    reason: str | None = Field(
        default=None, description="點名日程活動的原因；預設等級時是 null"
    )


def default_levels(concerns: list[Concern]) -> list[ConcernLevel]:
    """判斷不出需求等級時：第 1 名加強保障、其餘基本保障。"""
    return [
        ConcernLevel(concern=c, level="high" if i == 0 else "enough")
        for i, c in enumerate(concerns)
    ]


class ConcernAmountOut(BaseModel):
    concern: Concern
    amount: int = Field(
        description="對照到的保障項目額度加總（元），沒有就是 0；不是保證的理賠上限"
    )


class BenefitAmountOut(BaseModel):
    name: str = Field(description="保障項目（試算表原始名稱）")
    amount: int = Field(description="試算表顯示的額度（元），不是保證的理賠上限")


class PlanCardOut(BaseModel):
    plan_code: str
    name: str
    company: str
    url: str = Field(description="投保連結")
    sum_insured: int = Field(description="卡片目前選的身故保額（元）")
    recommended_sum_insured: int = Field(
        description="推薦保額：推薦時和方案一起選出的身故保額"
    )
    sum_insured_options: list[int] = Field(
        description="該方案在該天數下有報價的身故保額，由小到大"
    )
    premium: int = Field(description="目前保額、該天數的保費（元）")
    concern_amounts: list[ConcernAmountOut] = Field(description="依在意事項順序")


class ComparisonTableRowOut(BaseModel):
    key: str
    label: str
    explanation: str = Field(description="名詞解釋（固定文字）")
    concern: Concern | None = Field(
        description="這一列和使用者哪個在意事項有關；沒有就是 null"
    )
    recommended: list[BenefitAmountOut] = Field(description="空的代表未列")
    alternative: list[BenefitAmountOut] = Field(description="空的代表未列")


class RecommendationOut(BaseModel):
    recommended: PlanCardOut
    alternative: PlanCardOut = Field(description="對照方案（畫面上的「次推薦」）")
    concern_levels: list[ConcernLevel] = Field(description="需求等級，依在意事項順序")
    reason: str = Field(
        description="推薦理由：需求等級的原因加上固定句型的結論，不含數字"
    )
    basis: list[str] = Field(description="推薦依據（以推薦保額計算）")
    table: list[ComparisonTableRowOut] = Field(
        description="比較表（以卡片目前選的保額計算）"
    )


Pair = tuple[str, int]  # (方案, 身故保額)


def _select(quotes: Quotes, pairs: list[Pair], levels: list[ConcernLevel]) -> Pair:
    """依在意事項順序先篩「基本保障」、再篩「加強保障」，剩下的取保費最低的。

    pairs 已依 plans.json 順序、保額由小到大排好；min 取第一個最小值，所以同保費照這個順序。
    """
    for level, ratio in (
        ("enough", settings.RECOMMEND_ENOUGH_RATIO),
        ("high", settings.RECOMMEND_HIGH_RATIO),
    ):
        for c in (x.concern for x in levels if x.level == level):
            top = max(quotes.concern_amount(*p, c) for p in pairs)
            if top:  # 剩下的都沒有這項：略過
                pairs = [
                    p for p in pairs if quotes.concern_amount(*p, c) >= top * ratio
                ]
    return min(pairs, key=lambda p: quotes.premiums[p[0]][p[1]])


def pick(quotes: Quotes, levels: list[ConcernLevel]) -> tuple[Pair, Pair] | None:
    """回傳 ((推薦方案, 推薦保額), (對照方案, 對照保額))；有報價的方案不到兩個時是 None。"""
    order = [p.id for p in load_plans() if p.id in quotes.premiums]
    if len(order) < 2:
        return None
    pairs = [(code, s) for code in order for s in quotes.options(code)]
    rec = _select(quotes, pairs, levels)
    premium = quotes.premiums[rec[0]][rec[1]]
    others = [p for p in pairs if p[0] != rec[0]]
    cheaper = [p for p in others if quotes.premiums[p[0]][p[1]] < premium]
    return rec, _select(quotes, cheaper or others, levels)


def _money(n: int) -> str:
    return f"{n:,} 元"


def _reason(levels: list[ConcernLevel], source: Source, plan_name: str) -> str:
    conclusion = f"在這些需求都滿足的方案中，選保費最低的{plan_name}。"
    if source == "llm":
        reasons = "；".join((x.reason or "").rstrip("。.") for x in levels)
        return f"{reasons}。所以{conclusion}"
    others = "、其餘基本保障即可" if len(levels) > 1 else ""
    return f"依你排的順序，最在意的「{levels[0].concern}」需要加強保障{others}；{conclusion}"


def _basis(
    concerns: list[Concern],
    rec: tuple[int, ...],
    alt: tuple[int, ...],
    rec_premium: int,
    alt_premium: int,
) -> list[str]:
    def amount(n: int) -> str:
        return _money(n) if n else "未列"

    def verdict(a: int, b: int, higher: str, lower: str) -> str:
        return "兩者相同" if a == b else higher if a > b else lower

    lines = [
        f"{c}：{verdict(a, b, '推薦方案較高', '次推薦較高')}（{amount(a)} vs {amount(b)}）"
        for c, a, b in zip(concerns, rec, alt, strict=True)
    ]
    lines.append(
        f"保費：{verdict(rec_premium, alt_premium, '推薦方案較高', '推薦方案較低')}"
        f"（{_money(rec_premium)} vs {_money(alt_premium)}）"
    )
    return lines


def _table(
    quotes: Quotes, concerns: list[Concern], rec: PlanCardOut, alt: PlanCardOut
) -> list[ComparisonTableRowOut]:
    def items(card: PlanCardOut, keep) -> list[BenefitAmountOut]:
        return [
            BenefitAmountOut(name=b.name, amount=b.amount)
            for b in quotes.benefits.get((card.plan_code, card.sum_insured), [])
            if keep(b)
        ]

    def row(key: str, label: str, related: Concern | None, keep):
        return ComparisonTableRowOut(
            key=key,
            label=label,
            explanation=EXPLANATIONS[label],
            concern=related if related in concerns else None,
            recommended=items(rec, keep),
            alternative=items(alt, keep),
        )

    rows = [row("death", DEATH_LABEL, None, lambda b: b.name == DEATH_BENEFIT)]
    for c in BenefitCategory:
        if c is BenefitCategory.ILLNESS_INPATIENT:
            rows.append(
                row(
                    "illness",
                    ILLNESS_LABEL,
                    Concern.MEDICAL,
                    lambda b: b.category in ILLNESS_CATEGORIES,
                )
            )
        elif c is BenefitCategory.LIABILITY:
            # 只列推薦對照的責任保險，不含保的是台灣住處的住居所第三人責任
            rows.append(
                row(
                    c.name.lower(),
                    c,
                    CATEGORY_CONCERN[c],
                    lambda b: b.name in CONCERN_BENEFITS[Concern.LIABILITY],
                )
            )
        elif c not in ILLNESS_CATEGORIES:
            rows.append(
                row(
                    c.name.lower(),
                    c,
                    CATEGORY_CONCERN.get(c),
                    lambda b, c=c: b.category == c,
                )
            )
    rows += [
        row(c.name.lower(), label, c, lambda b, c=c: b.name in CONCERN_BENEFITS[c])
        for c, label in EXTRA_ROW_LABELS.items()
        if c in concerns
    ]
    # 有 concern 的列依在意事項順序排前面，其餘維持身故、保障類別的順序（穩定排序）
    return sorted(
        rows, key=lambda r: concerns.index(r.concern) if r.concern else len(concerns)
    )


def build(
    quotes: Quotes,
    levels: list[ConcernLevel],
    source: Source,
    recommended: Pair,
    alternative: Pair,
) -> RecommendationOut:
    """依存下的兩個方案與卡片保額組出卡片、理由、依據與比較表。

    卡片保額可能被使用者改過，推薦保額不存：依同樣的需求等級重跑一次選法取回。
    """
    plans = {p.id: p for p in load_plans()}
    concerns = [x.concern for x in levels]
    rule_sums = dict(pick(quotes, levels) or ())

    def card(plan_code: str, sum_insured: int) -> PlanCardOut:
        plan = plans[plan_code]
        options = quotes.options(plan_code)
        # 試算表換版後選法結果不同：推薦保額退回最低保額；已存的保額沒有報價時回到推薦保額
        rule_sum = rule_sums.get(plan_code, options[0])
        current = sum_insured if sum_insured in options else rule_sum
        return PlanCardOut(
            plan_code=plan_code,
            name=plan.name,
            company=plan.company,
            url=plan.url,
            sum_insured=current,
            recommended_sum_insured=rule_sum,
            sum_insured_options=options,
            premium=quotes.premiums[plan_code][current],
            concern_amounts=[
                ConcernAmountOut(
                    concern=c, amount=quotes.concern_amount(plan_code, current, c)
                )
                for c in concerns
            ],
        )

    rec, alt = card(*recommended), card(*alternative)

    def at_recommended(c: PlanCardOut) -> tuple[tuple[int, ...], int]:
        s = c.recommended_sum_insured
        amounts = tuple(quotes.concern_amount(c.plan_code, s, x) for x in concerns)
        return amounts, quotes.premiums[c.plan_code][s]

    (rec_amounts, rec_premium), (alt_amounts, alt_premium) = map(
        at_recommended, (rec, alt)
    )
    return RecommendationOut(
        recommended=rec,
        alternative=alt,
        concern_levels=levels,
        reason=_reason(levels, source, rec.name),
        basis=_basis(concerns, rec_amounts, alt_amounts, rec_premium, alt_premium),
        table=_table(quotes, concerns, rec, alt),
    )
