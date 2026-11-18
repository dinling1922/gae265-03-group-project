"""create_comparison：把使用者的一句話變成一筆比較紀錄與推薦（寫入，ADR-0034）。

後端 LLM 只做抽取（`app.domain.trip_extraction`）：目的地、日期、在意事項；抽取結果由程式驗證，
資訊不足就回 needs_more_info、什麼都不寫。成功時用網頁 `POST /comparisons` 同一套寫法存推薦與在意事項
（`_recommend`），回傳的推薦直接呼叫 `get_comparison`，不重算、不另寫第二套（ADR-0004：數字全由程式決定）。
"""

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from pydantic_core import PydanticCustomError

from app.api.routes.comparisons import ComparisonIn, _recommend
from app.domain import trip_extraction
from app.domain.benefits import Concern
from app.domain.destinations import Destination
from app.domain.trip_extraction import ExtractionFailed, Fixed, Interpretation
from app.mcp_server.context import tool_env
from app.mcp_server.registry import mcp_tool
from app.mcp_server.tools.get_comparison import GetComparisonOut, get_comparison
from app.models import Comparison

logger = logging.getLogger(__name__)

MAX_REQUEST_LENGTH = 500
LLM_FAILED = "暫時無法理解這段需求（語言模型服務失敗或逾時），沒有建立任何資料；請稍後再試，或改用 overrides 直接指定欄位。"
UNSUPPORTED_NOTE = "不納入推薦：目前推薦只依目的地、旅遊天數與在意事項"

Request = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_REQUEST_LENGTH
    ),
]


def _unique(concerns: list[Concern] | None) -> list[Concern] | None:
    if concerns is not None and len(set(concerns)) != len(concerns):
        raise PydanticCustomError("concerns_unique", "在意事項不能重複。")
    return concerns


class Overrides(BaseModel):
    """直接指定的結構化欄位；有值就用、不經 LLM 抽取該欄位（仍經同一套驗證）。"""

    destination: Destination | None = Field(default=None, description="目的地")
    depart_date: date | None = Field(default=None, description="出發日 YYYY-MM-DD")
    return_date: date | None = Field(
        default=None, description="回程日 YYYY-MM-DD；有給時不再使用原話裡的天數"
    )
    concerns: Annotated[list[Concern] | None, AfterValidator(_unique)] = Field(
        default=None,
        max_length=3,
        description="在意事項，依重要程度排序，最多 3 項、不重複",
    )


class UnderstoodOut(BaseModel):
    """後端理解的內容：只有通過驗證的部分，缺漏或不合格的欄位是 null 或空陣列。"""

    destination: Destination | None
    depart_date: date | None
    return_date: date | None
    days: int | None = Field(description="旅遊天數（含首尾），由程式計算")
    concerns: list[Concern] = Field(description="在意事項，依重要程度排序")


class MissingOut(BaseModel):
    field: Literal["destination", "depart_date", "trip_length", "concerns"]
    message: str = Field(description="可以直接轉述給使用者的提問")
    options: list[str] = Field(description="可用的選項提示；沒有選項時是空陣列")


class UnsupportedOut(BaseModel):
    request: str = Field(description="使用者提的需求（短語）")
    note: str


class CreateComparisonOut(BaseModel):
    """允許清單：只有這些欄位會離開後端。"""

    status: Literal["created", "needs_more_info"] = Field(
        description="created 表示已建立比較紀錄；needs_more_info 表示資訊不足、沒有建立任何資料"
    )
    comparison: GetComparisonOut | None = Field(
        description="created 時是新比較紀錄的旅行規劃與推薦（同 get_comparison）；否則是 null"
    )
    understood: UnderstoodOut
    missing: list[MissingOut] = Field(
        description="needs_more_info 時缺什麼與該怎麼問使用者；created 時是空陣列"
    )
    unsupported: list[UnsupportedOut] = Field(
        description="使用者提了但不納入推薦的需求"
    )


def _out(
    result: Interpretation, comparison: GetComparisonOut | None
) -> CreateComparisonOut:
    return CreateComparisonOut(
        status="created" if comparison else "needs_more_info",
        comparison=comparison,
        understood=UnderstoodOut(
            destination=result.destination,
            depart_date=result.depart_date,
            return_date=result.return_date,
            days=result.days,
            concerns=result.concerns,
        ),
        missing=[
            MissingOut(field=m.name, message=m.message, options=m.options)
            for m in result.missing
        ],
        unsupported=[
            UnsupportedOut(request=u, note=UNSUPPORTED_NOTE) for u in result.unsupported
        ],
    )


@mcp_tool(scope="write", calls_llm=True)
async def create_comparison(
    request: Request, overrides: Overrides | None = None
) -> CreateComparisonOut:
    """依使用者的原話建立一筆比較紀錄並回傳推薦方案（功能一）。需要有寫入權（write）的權杖。

    request 放使用者的原話（例如「下週五去東京 5 天，最擔心班機延誤和行李遺失」）。後端用語言模型只抽取
    目的地、出發與回程日期、最多 3 個在意事項，並由程式驗證；推薦與保費全由程式依報價表決定，模型不產生任何數字。
    overrides 可直接指定 destination、depart_date、return_date、concerns，有給的欄位不經模型抽取。

    status 是 needs_more_info 時沒有建立任何資料：missing 列出缺什麼、該怎麼問使用者（含可用選項），
    請向使用者問清楚後帶 overrides 或補充的原話再呼叫一次。status 是 created 時 comparison 是新比較紀錄
    （形狀同 get_comparison），understood 是後端理解的內容，請先給使用者核對；建錯就再建一筆。
    unsupported 列出使用者提了但不納入推薦的需求（例如最便宜、年齡、預算）。
    旅遊天數只支援 4–10 天，出發日要在今天（台北時間）起一年內。額度與保費是試算表的金額，不是保證的理賠上限。
    """
    fixed = Fixed(
        destination=overrides.destination.value
        if overrides and overrides.destination
        else None,
        depart_date=overrides.depart_date if overrides else None,
        return_date=overrides.return_date if overrides else None,
        concerns=[c.value for c in overrides.concerns]
        if overrides and overrides.concerns is not None
        else None,
    )
    try:
        result = await trip_extraction.extract(
            request, trip_extraction.today_taipei(), fixed
        )
    except ExtractionFailed as e:
        raise ToolError(LLM_FAILED) from e
    if not result.complete:
        return _out(result, None)

    assert result.destination and result.depart_date and result.return_date
    env = tool_env()
    try:
        body = ComparisonIn(
            destination=result.destination,
            depart_date=result.depart_date,
            return_date=result.return_date,
            concerns=result.concerns,
        )
    except ValueError as e:  # 前面都驗證過了；走到這裡是程式的缺口，不寫入
        logger.exception("create_comparison 最後驗證失敗")
        raise ToolError("旅行規劃不合格，沒有建立任何資料") from e
    async with env.session_maker() as session:
        comparison = Comparison(user_id=env.identity.user.id, **body.model_dump())
        try:
            await _recommend(session, comparison)
        except HTTPException as e:  # 例如該天數有報價的方案不到兩個
            raise ToolError(f"無法產生推薦：{e.detail}") from e
        session.add(comparison)
        await session.commit()
        comparison_id = comparison.id
    return _out(result, await get_comparison(comparison_id))
