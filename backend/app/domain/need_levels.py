"""需求等級（spec #457、ADR-0034）：LLM 依旅遊日程替使用者選的在意事項判斷「加強保障」或「基本保障」。

LLM 不看方案資料、不產生金額。輸出由程式驗證：恰好是使用者選的那幾項、不重複，
原因裡的數字都要出自日程（天數、日期、時間），「第 N 天」不能超出行程。失敗、逾時或不合格就整次改用預設等級。
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domain.benefits import Concern
from app.domain.numbers import unsupported_numbers
from app.domain.recommendation import ConcernLevel, Level, Source, default_levels
from app.models import Comparison, ItineraryActivity

logger = logging.getLogger(__name__)


class _Level(BaseModel):
    concern: Concern
    level: Level = Field(description="high = 加強保障；enough = 基本保障")
    reason: str = Field(
        description="點名日程活動的一句原因：用「第 N 天」不寫日期，40 字以內，不含金額"
    )


class _Levels(BaseModel):
    """給 structured output 用的外層；項目是否齊全由 validated 檢查。"""

    levels: list[_Level] = Field(description="使用者選的每個在意事項各一項")


# 颱風季（7～10 月）出發由程式判斷，LLM 只看日程有沒有標「颱風季出發」
_TYPHOON_MONTHS = range(7, 11)

# 收到比較紀錄與日程活動，回傳 LLM 的原始輸出（_Levels 或同形狀的 dict）
NeedLeveler = Callable[[Comparison, Sequence[ItineraryActivity]], Awaitable[object]]

SYSTEM_PROMPT = """你是旅遊保險推薦助理。依旅遊日程，替使用者選的每個在意事項判斷需求等級，各寫一句原因。

需求等級只有兩種：
- high（加強保障）：日程裡有下表列出的加重情況，保障額度越高越值得多付保費。
- enough（基本保障）：日程裡沒有加重情況，有基本保障即可。

判斷方法（照做，不要自由發揮）：
1. 逐項對照下表。只認表上寫的情況與舉例，表上沒列的一律不算；不因為「旅途中可能發生」就判 high。
2. 日程裡至少有一個活動符合該項的任一加重情況 → high；都不符合 → enough。
3. 每趟旅程都有的直飛去回程航班、一般觀光、逛街、藥妝店、餐廳用餐，不算加重情況。

| 在意事項 | 加重情況（符合任一即 high） |
|---|---|
| 突然生病就醫 | 生蠔、生肉、路邊攤或夜市小吃；登山、健行、滑雪、潛水、泛舟等戶外活動；高海拔（2,500 公尺以上） |
| 需要緊急送醫或家屬趕來 | 登山、健行、滑雪、潛水、泛舟；郵輪；偏遠山區；從抵達地再搭船或飛機前往的離島 |
| 行程取消或更改 | 已預付不可退款的住宿、餐廳或行程；預購的票券（演唱會、球賽、表演）；日程標示「颱風季出發」 |
| 班機延誤 | 轉機；日程標示「颱風季出發」 |
| 行李延誤 | 轉機；托運 2 件以上或大型行李；抵達當天或隔天就要用托運的裝備（滑雪、潛水、正式服裝） |
| 行李遺失或損壞 | 轉機；托運大量採購或貴重物品 |
| 證件遺失 | 祭典、花火大會、演唱會、跨年等大型人潮活動；同一趟旅程跨兩個以上國家 |
| 信用卡盜刷 | 在精品店、百貨公司或電器行購物（一律視為大額消費） |
| 不小心弄傷別人或弄壞東西 | 滑雪、腳踏車等可能撞到人的運動 |

原因的寫法：
- 一律用日程標的「第 N 天」（阿拉伯數字）指稱日子，不寫日期；40 字以內；不寫金額、額度、保費，也不說會不會理賠。
- high：點名符合的活動與情況，例如「第 2 天保津川泛舟，屬於水上戶外活動」。
- enough：寫最相關的活動與它缺少的情況，例如「第 1 天直飛抵達，沒有轉機」「第 3 天在藥妝店購物，不是大額消費」。
- 原因是給使用者看的：不寫「加重情況」「符合」「表上」這類判斷過程的用語。
- 使用者選的每個在意事項都要判斷，恰好一項一筆，名稱一字不改，不可新增或重複。"""


def user_prompt(c: Comparison, activities: Sequence[ItineraryActivity]) -> str:
    """日程每行前面標「第 N 天」：LLM 自己從日期推天數會算錯（曾把 12/21 寫成第 21 天）。"""
    place = f"{c.destination}（{c.city}）" if c.city else c.destination
    days = (c.return_date - c.depart_date).days + 1
    period = f"{c.depart_date.isoformat()}–{c.return_date.isoformat()}"
    lines = [f"目的地：{place}", f"日期：{period}，共 {days} 天"]
    if c.depart_date.month in _TYPHOON_MONTHS:
        lines.append("颱風季出發")
    lines.append("日程：")
    lines += [
        f"第 {(a.date - c.depart_date).days + 1} 天 {a.hour:02d}:00（{a.duration:g}h）"
        f"[{a.category}] {a.title}｜{a.location}｜{a.description}"
        for a in activities
    ]
    lines.append(f"使用者選的在意事項（依重要程度）：{'、'.join(c.concerns)}")
    return "\n".join(lines)


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


_DAY = re.compile(r"第\s*(\d+)\s*天")
# prompt 要求用「第 N 天」；日期的數字也在日程裡、能過白名單，所以另外擋
_DATE = re.compile(r"\d+\s*月\s*\d+\s*日|\d+/\d+")


def _validated(
    raw: object, concerns: list[Concern], sources: list[str], days: int
) -> list[ConcernLevel]:
    """項目不符、原因含日程以外的數字或超出行程的天數時丟 ValueError（ValidationError 也是 ValueError）。

    天數另外檢查：數字白名單只看數字有沒有出現過，日期裡的 21 會讓「第 21 天」過關。
    """
    items = _Levels.model_validate(raw).levels
    by_concern = {x.concern: x for x in items}
    if len(items) != len(concerns) or set(by_concern) != set(concerns):
        raise ValueError(f"需求等級要恰好是使用者選的在意事項：{raw!r}")
    for x in items:
        if bad := unsupported_numbers(x.reason, sources):
            raise ValueError(f"原因含日程以外的數字 {bad}：{x.reason!r}")
        if _DATE.search(x.reason):
            raise ValueError(f"原因要用「第 N 天」、不寫日期：{x.reason!r}")
        if any(not 1 <= int(n) <= days for n in _DAY.findall(x.reason)):
            raise ValueError(f"原因的天數超出 {days} 天的行程：{x.reason!r}")
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
        # 清單上的數字是程式寫的，原因引用也算有出處；只放這幾段，避免整份 prompt 的數字都過關
        sources = ["2,500 公尺", user_prompt(c, activities)]
        sources += [f"第 {n} 天" for n in range(1, days + 1)]
        try:
            async with asyncio.timeout(settings.RECOMMEND_LLM_TIMEOUT):
                raw = await leveler(c, activities)
            return _validated(raw, concerns, sources, days), "llm"
        except Exception:  # LLM 失敗不該擋住推薦：改用預設等級
            logger.exception("判斷需求等級失敗（comparison %s）", c.id)
    return default_levels(concerns), "default"
