"""list_comparisons：呼叫者本人的比較紀錄（最新在前）。"""

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, Field
from sqlmodel import col, select

from app.mcp_server.context import tool_env
from app.mcp_server.registry import mcp_tool
from app.models import Comparison

MAX_LIMIT = 50


class ComparisonSummary(BaseModel):
    """允許清單：只有這些欄位會離開後端。不含城市、在意事項等使用者輸入的內容。"""

    id: uuid.UUID
    destination: str
    depart_date: date
    return_date: date
    days: int = Field(description="旅遊天數（含出發與回程當天），由程式計算")
    has_recommendation: bool = Field(description="是否已有推薦結果")
    created_at: datetime
    updated_at: datetime


class ListComparisonsOut(BaseModel):
    comparisons: list[ComparisonSummary]


@mcp_tool
async def list_comparisons(
    limit: Annotated[
        int, Field(ge=1, le=MAX_LIMIT, description="最多回傳幾筆，1 到 50")
    ] = 10,
) -> ListComparisonsOut:
    """列出使用者自己的旅遊保險比較紀錄，最新的在前。

    每筆包含比較紀錄代碼、目的地、出發與回程日期、天數、是否已有推薦結果、建立與更新時間。
    只讀取，不會建立或修改任何資料。要看某次比較的推薦內容請用 get_comparison。
    """
    env = tool_env()
    async with env.session_maker() as session:
        rows = await session.exec(
            select(Comparison)
            .where(Comparison.user_id == env.identity.user.id)
            .order_by(col(Comparison.created_at).desc())
            .limit(limit)
        )
        return ListComparisonsOut(
            comparisons=[
                ComparisonSummary(
                    id=c.id,
                    destination=c.destination,
                    depart_date=c.depart_date,
                    return_date=c.return_date,
                    days=(c.return_date - c.depart_date).days + 1,
                    has_recommendation=c.recommended_plan_code is not None
                    and c.alternative_plan_code is not None,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
                for c in rows
            ]
        )
