"""需求等級（spec #457、ADR-0032）：LLM 依旅遊日程替使用者選的在意事項判斷「越高越好」或「有就好」。

LLM 不看方案資料、不產生金額。輸出由程式驗證：恰好是使用者選的那幾項、不重複，
原因裡的數字都要出自日程（天數、日期、時間）。失敗、逾時或不合格就整次改用預設等級。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domain.benefits import Concern
from app.domain.concern_suggestions import user_prompt as itinerary_prompt
from app.domain.numbers import unsupported_numbers
from app.domain.recommendation import ConcernLevel, Level, Source, default_levels
from app.models import Comparison, ItineraryActivity

logger = logging.getLogger(__name__)


class _Level(BaseModel):
    concern: Concern
    level: Level = Field(description="high = 越高越好；enough = 有就好")
    reason: str = Field(description="點名日程活動的一句原因，40 字以內，不含金額")


class _Levels(BaseModel):
    """給 structured output 用的外層；項目是否齊全由 validated 檢查。"""

    levels: list[_Level] = Field(description="使用者選的每個在意事項各一項")


# 收到比較紀錄與日程活動，回傳 LLM 的原始輸出（_Levels 或同形狀的 dict）
NeedLeveler = Callable[[Comparison, Sequence[ItineraryActivity]], Awaitable[object]]

SYSTEM_PROMPT = """你是旅遊保險推薦助理。依旅遊日程，替使用者選的每個在意事項判斷需求等級，各寫一句原因。

需求等級只有兩種：
- high（越高越好）：日程裡有明顯會碰到這項風險的活動，保障額度越高越值得多付保費。例：要滑雪、登山時的突然生病就醫。
- enough（有就好）：有一定保障即可，不值得為它多付保費。

規則：
- 使用者選的每個在意事項都要判斷，恰好一項一筆，名稱一字不改，不可新增或重複。
- 原因必須點名日程中的具體活動（可寫「第 2 天」或日期＋活動標題），40 字以內。
- 不寫金額、額度、保費，也不說會不會理賠。"""


def user_prompt(c: Comparison, activities: Sequence[ItineraryActivity]) -> str:
    concerns = "、".join(c.concerns)
    return f"{itinerary_prompt(c, activities)}\n使用者選的在意事項（依重要程度）：{concerns}"


async def llm_need_levels(
    c: Comparison, activities: Sequence[ItineraryActivity]
) -> object:
    from app.core.llm import get_chat_model

    model = get_chat_model().with_structured_output(_Levels)
    return await model.ainvoke(
        [("system", SYSTEM_PROMPT), ("human", user_prompt(c, activities))]
    )


def get_need_leveler() -> NeedLeveler:
    """FastAPI dependency；測試換成假的。"""
    return llm_need_levels


def _validated(
    raw: object, concerns: list[Concern], sources: list[str]
) -> list[ConcernLevel]:
    """項目不符或原因含日程以外的數字時丟 ValueError（ValidationError 也是 ValueError）。"""
    items = _Levels.model_validate(raw).levels
    by_concern = {x.concern: x for x in items}
    if len(items) != len(concerns) or set(by_concern) != set(concerns):
        raise ValueError(f"需求等級要恰好是使用者選的在意事項：{raw!r}")
    for x in items:
        if bad := unsupported_numbers(x.reason, sources):
            raise ValueError(f"原因含日程以外的數字 {bad}：{x.reason!r}")
    return [
        ConcernLevel(concern=c, level=by_concern[c].level, reason=by_concern[c].reason)
        for c in concerns
    ]


async def judge(
    c: Comparison,
    activities: Sequence[ItineraryActivity],
    leveler: NeedLeveler,
    days: int,
) -> tuple[list[ConcernLevel], Source]:
    """回傳需求等級與來源；日程沒有活動時不呼叫 LLM。"""
    concerns = [Concern(x) for x in c.concerns]
    if activities:
        sources = [user_prompt(c, activities)]
        sources += [f"第 {n} 天" for n in range(1, days + 1)]
        try:
            async with asyncio.timeout(settings.RECOMMEND_LLM_TIMEOUT):
                raw = await leveler(c, activities)
            return _validated(raw, concerns, sources), "llm"
        except Exception:  # LLM 失敗不該擋住推薦：改用預設等級
            logger.exception("判斷需求等級失敗（comparison %s）", c.id)
    return default_levels(concerns), "default"
