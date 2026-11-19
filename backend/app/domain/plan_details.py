"""方案的保障明細、可選保額與保費（查詢用；網站路由與 MCP 工具共用，不各自重算）。

資料來源是 plan_benefit、plan_premium 兩張表（易安網試算表，spec #48）；沒有的就是沒有，不推估（ADR-0004）。
"""

from dataclasses import dataclass

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.benefits import BenefitCategory
from app.models import PlanBenefit, PlanPremium


@dataclass(frozen=True)
class BenefitLine:
    name: str
    category: BenefitCategory | None
    amount: int  # 試算表顯示的額度（元），不是保證的理賠上限


@dataclass(frozen=True)
class PremiumQuote:
    days: int
    premium: int  # 元


async def sum_insured_options(session: AsyncSession, plan_id: str) -> list[int]:
    """方案可投保的身故保額（元），由小到大；各方案不同（ADR-0018）。"""
    return list(
        (
            await session.exec(
                select(PlanPremium.sum_insured)
                .where(PlanPremium.plan_code == plan_id)
                .distinct()
                .order_by(col(PlanPremium.sum_insured))
            )
        ).all()
    )


async def plan_benefits(
    session: AsyncSession, plan_id: str, sum_insured: int
) -> list[BenefitLine]:
    """方案在某身故保額下的完整保障明細，順序同試算表；沒有資料回空清單。"""
    rows = (
        await session.exec(
            select(PlanBenefit)
            .where(
                PlanBenefit.plan_code == plan_id, PlanBenefit.sum_insured == sum_insured
            )
            .order_by(col(PlanBenefit.position))
        )
    ).all()
    return [
        BenefitLine(
            name=r.name,
            category=BenefitCategory(r.category) if r.category else None,
            amount=r.amount,
        )
        for r in rows
    ]


async def plan_premium_quotes(
    session: AsyncSession, plan_id: str, sum_insured: int
) -> list[PremiumQuote]:
    """方案在某身故保額下，各保險天數的保費（元），天數由少到多。"""
    rows = (
        await session.exec(
            select(PlanPremium)
            .where(
                PlanPremium.plan_code == plan_id, PlanPremium.sum_insured == sum_insured
            )
            .order_by(col(PlanPremium.days))
        )
    ).all()
    return [PremiumQuote(days=r.days, premium=r.premium) for r in rows]
