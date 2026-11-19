"""ask_plan_question：對某次比較的推薦方案與次推薦提問（唯讀範圍，會呼叫後端 LLM，ADR-0034）。

行為與網站 1-4 方案問答完全一致：呼叫同一個 `answer_for_comparison`（改寫、guard、兩個方案各自檢索條文、
LLM 依條文回答、數字白名單未通過就只列條款原文），問題長度與 history 上限也沿用；不存檔。
輸出用專用的允許清單模型，不重用 API 回應模型（ADR-0032）。
"""

import logging
import uuid
from typing import Annotated

from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.api.routes.conversations import answer_model
from app.api.routes.questions import (
    MAX_HISTORY_TURNS,
    PlanCitationsOut,
    Question,
    QuestionAnswerOut,
    TurnIn,
    answer_for_comparison,
    question_guard,
)
from app.mcp_server.context import tool_env
from app.mcp_server.registry import mcp_tool
from app.mcp_server.tools.get_comparison import NOT_FOUND
from app.models import Comparison

logger = logging.getLogger(__name__)

UNAVAILABLE = "問答服務暫時無法使用，請稍後再試"


class CitationOut(BaseModel):
    """條款出處；不含內部代碼與檢索分數。"""

    insurance_code: str = Field(description="險種代碼")
    insurance_name: str
    article_no: int = Field(description="條號")
    title: str = Field(description="條文標題")
    section: str | None = Field(description="所屬的章節")
    page_start: int
    page_end: int
    excerpt: str = Field(description="條款原文")


class PlanSourcesOut(BaseModel):
    plan_code: str
    plan_name: str
    recommended: bool = Field(description="true 是推薦方案，false 是次推薦")
    citations: list[CitationOut] = Field(description="這個方案檢索到的條文，可能是空的")


class AskPlanQuestionOut(BaseModel):
    """允許清單：只有這些欄位會離開後端。"""

    answer: str
    original_only: bool = Field(
        description="數字白名單未通過或 LLM 失敗，answer 只列條款原文，不是白話說明"
    )
    citations: list[PlanSourcesOut] = Field(
        description="依方案分組的條文依據（推薦在前）；擋下或條款未載明時是空陣列"
    )


def _out(answer: QuestionAnswerOut) -> AskPlanQuestionOut:
    def plan(p: PlanCitationsOut) -> PlanSourcesOut:
        return PlanSourcesOut(
            plan_code=p.plan_code,
            plan_name=p.plan_name,
            recommended=p.recommended,
            citations=[
                CitationOut(
                    insurance_code=c.insurance_code,
                    insurance_name=c.insurance_name,
                    article_no=c.article_no,
                    title=c.title,
                    section=c.section,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    excerpt=c.excerpt,
                )
                for c in p.citations
            ],
        )

    return AskPlanQuestionOut(
        answer=answer.answer,
        original_only=answer.original_only,
        citations=[plan(p) for p in answer.citations],
    )


@mcp_tool(calls_llm=True)
async def ask_plan_question(
    comparison_id: uuid.UUID,
    question: Question,
    history: Annotated[list[TurnIn], Field(max_length=MAX_HISTORY_TURNS)] | None = None,
) -> AskPlanQuestionOut:
    """針對使用者某次比較的推薦方案與次推薦，詢問條款內容（與網站的方案問答一致）。

    比較紀錄代碼可由 list_comparisons 取得，且要已有推薦方案。只依條款原文與保障明細回答，並附條文出處；
    問題超出方案與保險範圍會被擋下，條款沒寫會回「條款未載明」。回答若含條款沒有的數字，會退回只列條款原文
    （original_only 為 true）。history 放前幾輪的問題與回答（舊的在前，最多 3 輪）供追問用；不會儲存任何內容。
    問題最多 300 字。額度是試算表的金額，不是保證的理賠上限，實際以保險公司核定為準。
    找不到或不是本人的紀錄一律回「找不到這筆比較紀錄」。
    """
    env = tool_env()
    async with env.session_maker() as session:
        comparison = await session.get(Comparison, comparison_id)
        if comparison is None or comparison.user_id != env.identity.user.id:
            raise ToolError(NOT_FOUND)
        try:
            model = answer_model()
            guard = question_guard()
        except Exception as e:  # 供應商沒設定好等；細節不外洩
            logger.exception("ask_plan_question 無法建立 LLM")
            raise ToolError(UNAVAILABLE) from e
        try:
            answer = await answer_for_comparison(
                session, comparison, model, guard, question, history or []
            )
        except HTTPException as e:  # 還沒有推薦方案（網站是 422）
            raise ToolError(str(e.detail)) from e
    return _out(answer)
