"""get_comparison：某次比較紀錄的旅行規劃、在意事項與推薦方案（唯讀）。

推薦與數字沿用網頁 `GET /comparisons/{id}` 的同一套來源（`load_quotes`、`recommendation.build`），
只讀已存的推薦結果：沒有推薦就回 incomplete，不替使用者觸發計算、不呼叫 LLM（ADR-0004、ADR-0032）。
"""

import uuid
from datetime import date
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.api.routes.comparisons import _levels, _saved_plans, load_quotes, trip_days
from app.domain.benefits import Concern
from app.domain.plans import get_plan
from app.domain.recommendation import PlanCardOut, build
from app.mcp_server.context import tool_env
from app.mcp_server.registry import mcp_tool
from app.models import Comparison

# 不存在與不是本人共用這一句，無法由回應探測紀錄存在與否
NOT_FOUND = "找不到這筆比較紀錄"

MISSING_CONCERNS = "尚未選在意事項（請使用者在網頁的比較流程選擇在意事項）"
MISSING_RECOMMENDATION = "尚未產生推薦方案（請使用者在網頁重新儲存這次比較）"
RECOMMENDATION_STALE = "推薦的方案已不在目前的報價中（請使用者在網頁重新確認這次比較）"


class TripPlanOut(BaseModel):
    """旅行規劃：不含城市與旅遊日程等使用者輸入的自由文字。"""

    destination: str
    depart_date: date
    return_date: date
    days: int = Field(description="旅遊天數（含出發與回程當天），由程式計算")


class PlanOut(BaseModel):
    plan_id: str = Field(description="方案代碼，可用在 get_plan_details 查保障明細")
    name: str = Field(description="方案名稱")
    company: str = Field(description="保險公司")
    sum_insured: int = Field(description="目前選的身故保額（元）")
    premium: int = Field(
        description="該保額、該旅遊天數的保費（元），由程式依報價表取得"
    )


class GetComparisonOut(BaseModel):
    """允許清單：只有這些欄位會離開後端。不含推薦理由與依據文字、比較表、投保連結、事件、保單。"""

    id: uuid.UUID
    status: Literal["complete", "incomplete"] = Field(
        description="complete 表示已有推薦方案；incomplete 表示還缺 missing 說明的那一步"
    )
    missing: str | None = Field(description="incomplete 時缺哪一步；complete 時是 null")
    trip: TripPlanOut
    concerns: list[Concern] = Field(description="在意事項，依重要程度排序")
    recommended: PlanOut | None = Field(description="推薦方案；incomplete 時是 null")
    alternative: PlanOut | None = Field(
        description="次推薦（對照方案）；incomplete 時是 null"
    )


def _plan(card: PlanCardOut) -> PlanOut:
    return PlanOut(
        plan_id=card.plan_code,
        name=card.name,
        company=card.company,
        sum_insured=card.sum_insured,
        premium=card.premium,
    )


@mcp_tool
async def get_comparison(comparison_id: uuid.UUID) -> GetComparisonOut:
    """取得使用者自己的某一次比較紀錄：旅行規劃（目的地、日期、天數）、在意事項（依重要程度排序）、
    推薦方案與次推薦（方案名稱、保險公司、保額、保費）。

    比較紀錄代碼可由 list_comparisons 取得。數字都由程式依報價表算出，與網頁一致；額度與保費是
    試算表的金額，不是保證的理賠上限。若這次比較還沒有推薦結果，status 是 incomplete，
    missing 說明缺哪一步，此時不會建立推薦或修改任何資料。找不到或不是本人的紀錄一律回「找不到這筆比較紀錄」。
    """
    env = tool_env()
    async with env.session_maker() as session:
        c = await session.get(Comparison, comparison_id)
        if c is None or c.user_id != env.identity.user.id:
            raise ToolError(NOT_FOUND)
        trip = TripPlanOut(
            destination=c.destination,
            depart_date=c.depart_date,
            return_date=c.return_date,
            days=trip_days(c),
        )
        concerns = [Concern(x) for x in c.concerns]

        def incomplete(missing: str) -> GetComparisonOut:
            return GetComparisonOut(
                id=c.id,
                status="incomplete",
                missing=missing,
                trip=trip,
                concerns=concerns,
                recommended=None,
                alternative=None,
            )

        saved = _saved_plans(c)
        if saved is None:
            return incomplete(MISSING_RECOMMENDATION if concerns else MISSING_CONCERNS)
        quotes = await load_quotes(session, trip_days(c))
        if any(get_plan(code) is None or not quotes.options(code) for code, _ in saved):
            return incomplete(RECOMMENDATION_STALE)
        rec = build(quotes, *_levels(c), *saved)
        return GetComparisonOut(
            id=c.id,
            status="complete",
            missing=None,
            trip=trip,
            concerns=concerns,
            recommended=_plan(rec.recommended),
            alternative=_plan(rec.alternative),
        )
