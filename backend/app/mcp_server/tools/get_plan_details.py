"""get_plan_details：方案的保障項目、額度、可選保額與保費（唯讀，全是程式資料）。

與網頁同一批領域函式（`app.domain.plan_details`）：保障項目與額度同 `GET /plans/{id}/benefits`，
可選保額同 `GET /plans/{id}/sum-insured-options`，保費是同一張報價表；工具不重算、不推估（ADR-0004、ADR-0034）。
方案是公開的固定資料，不含使用者資料，所以不需要比對權杖擁有者。
"""

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.domain.plan_details import (
    plan_benefits,
    plan_premium_quotes,
    sum_insured_options,
)
from app.domain.plans import get_plan
from app.mcp_server.context import tool_env
from app.mcp_server.registry import mcp_tool

PLAN_NOT_FOUND = "找不到這個方案"
UPLOAD_ONLY = "這個方案只能由上傳保單比對得到，沒有保費與保障資料"
NO_SUM_INSURED_HINT = "請用 sum_insured 指定其中一個身故保額，才會回保障項目與保費"


class PlanBenefitOut(BaseModel):
    name: str = Field(description="保障項目名稱（試算表的原始名稱）")
    category: str | None = Field(
        description="保障類別（如班機延誤、海外疾病住院）；不在保障重點內的項目是 null"
    )
    amount: int = Field(
        description="額度（元），是試算表顯示的金額，不是保證的理賠上限"
    )


class PremiumQuoteOut(BaseModel):
    days: int = Field(description="保險天數")
    premium: int = Field(description="保費（元），由報價表取得")


class GetPlanDetailsOut(BaseModel):
    """允許清單：只有這些欄位會離開後端。不含條款全文、投保連結與任何使用者資料。"""

    plan_id: str
    name: str = Field(description="方案名稱")
    company: str = Field(description="保險公司")
    sum_insured_options: list[int] = Field(
        description="這個方案可選的身故保額（元），由小到大"
    )
    sum_insured: int | None = Field(
        description="保障項目與保費對應的身故保額（元）；沒指定是 null"
    )
    benefits: list[PlanBenefitOut] = Field(
        description="該保額下的完整保障明細，順序同網頁；沒指定保額是空清單"
    )
    premiums: list[PremiumQuoteOut] = Field(
        description="該保額下各保險天數的保費，天數由少到多；沒指定保額是空清單"
    )
    note: str | None = Field(description="需要補充的說明；沒有就是 null")


@mcp_tool
async def get_plan_details(
    plan_id: str, sum_insured: int | None = None
) -> GetPlanDetailsOut:
    """取得某個保險方案的保障明細：方案名稱與保險公司、可選的身故保額、該保額下的保障項目與額度、各保險天數的保費。

    plan_id 可由 get_comparison 的結果取得（推薦方案與次推薦）。不帶 sum_insured 時只回可選保額
    （sum_insured_options），請從中挑一個再呼叫一次；帶了才會回該保額下的保障項目與保費。
    額度與保費都是易安網試算表的金額，由程式提供、與網頁一致；額度不是保證的理賠上限，
    未列的保障不等於沒有保障。保單專用方案（只能由上傳保單比對到）沒有保費與保障資料，找不到的方案會說明找不到。
    """
    plan = get_plan(plan_id)
    if plan is None:
        raise ToolError(PLAN_NOT_FOUND)
    if plan.upload_only:
        raise ToolError(UPLOAD_ONLY)
    env = tool_env()
    async with env.session_maker() as session:
        options = await sum_insured_options(session, plan.id)
        if sum_insured is None:
            return GetPlanDetailsOut(
                plan_id=plan.id,
                name=plan.name,
                company=plan.company,
                sum_insured_options=options,
                sum_insured=None,
                benefits=[],
                premiums=[],
                note=NO_SUM_INSURED_HINT,
            )
        lines = await plan_benefits(session, plan.id, sum_insured)
        if not lines:
            raise ToolError(
                f"這個方案沒有身故保額 {sum_insured} 元的資料；"
                f"可選的身故保額（元）：{', '.join(str(o) for o in options)}"
            )
        quotes = await plan_premium_quotes(session, plan.id, sum_insured)
    return GetPlanDetailsOut(
        plan_id=plan.id,
        name=plan.name,
        company=plan.company,
        sum_insured_options=options,
        sum_insured=sum_insured,
        benefits=[
            PlanBenefitOut(
                name=b.name,
                category=b.category.value if b.category else None,
                amount=b.amount,
            )
            for b in lines
        ],
        premiums=[PremiumQuoteOut(days=q.days, premium=q.premium) for q in quotes],
        note=None,
    )
