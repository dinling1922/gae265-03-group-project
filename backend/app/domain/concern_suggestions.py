"""推薦的在意事項（spec #427）：依旅遊日程用 LLM 挑 3 項在意事項，各附一句點名活動的理由。

LLM 只挑清單裡的名稱與寫理由，不產生任何金額（ADR-0004）。輸出由程式再驗證一次：
恰好 3 項、都在 Concern 清單裡、不重複；不合格就當作失敗。
"""

from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel, Field

from app.domain.benefits import Concern
from app.models import Comparison, ItineraryActivity

COUNT = 3


class Suggestion(BaseModel):
    concern: Concern
    reason: str = Field(description="點名日程活動（日期＋標題）的一句理由，40 字以內")


class Suggestions(BaseModel):
    """給 structured output 用的外層；不加數量限制，數量由 validated 檢查。"""

    suggestions: list[Suggestion] = Field(description="恰好 3 項，依可能性由高到低")


# 收到比較紀錄與日程活動，回傳 LLM 的原始輸出（Suggestions 或同形狀的 dict）
Suggester = Callable[[Comparison, Sequence[ItineraryActivity]], Awaitable[object]]

SYSTEM_PROMPT = """你是旅遊保險推薦助理。從下面的在意事項清單挑出這趟旅行最可能遇到的 3 項，依可能性由高到低排序，每項寫一句理由。

規則：
- 只能從清單挑，名稱一字不改；3 項不可重複。
- 理由必須點名日程中的具體活動（日期＋活動標題），40 字以內，語氣具體、不嚇人。
- 不提金額、保額、保費，也不說會不會理賠。
- 一律挑 3 項；沒有強相關時，用一般出國旅行都會遇到的風險補滿（例如有搭機就補班機延誤）。

在意事項清單（定義；例：會觸發的活動）：
- 突然生病就醫：旅途中生病就醫（例如吃壞肚子、發燒），不含意外受傷。例：生食、路邊攤、夜市、深夜喝酒
- 需要緊急送醫或家屬趕來：在偏遠或戶外地點重病、重傷，需要救援、轉送或家屬飛來照顧。例：登山、泛舟、潛水
- 行程取消或更改：出發前或旅途中因生病、天災、罷工必須取消或改期，且有預付不可退的費用。例：預付訂房、演唱會票、颱風季出發
- 班機延誤：搭機時班機延誤。例：搭機、轉機、紅眼班機
- 行李延誤：托運行李沒跟著抵達。例：托運行李、轉機
- 行李遺失或損壞：托運行李遺失或被摔壞。例：托運易碎品、大量採買後托運
- 證件遺失：護照或簽證在人潮多、常要拿出證件的地方遺失或被偷。例：退稅、祭典、夜市、大眾運輸
- 信用卡盜刷：大量刷卡、在攤販或觀光區消費、在海外網站或 App 綁卡。例：藥妝電器採購、outlet、樂園 App 買票
- 不小心弄傷別人或弄壞東西：造成他人受傷或財物損失（例如弄壞租借品、撞到人），不含自己受傷或自己的東西壞掉。例：租和服、陶藝體驗、租腳踏車、民宿"""


def user_prompt(c: Comparison, activities: Sequence[ItineraryActivity]) -> str:
    place = f"{c.destination}（{c.city}）" if c.city else c.destination
    lines = [
        f"目的地：{place}",
        f"日期：{c.depart_date.isoformat()}–{c.return_date.isoformat()}",
        "日程：",
    ]
    lines += [
        f"{a.date.isoformat()} {a.hour:02d}:00（{a.duration:g}h）[{a.category}] "
        f"{a.title}｜{a.location}｜{a.description}"
        for a in activities
    ]
    return "\n".join(lines)


async def llm_suggest(c: Comparison, activities: Sequence[ItineraryActivity]) -> object:
    from app.core.llm import get_chat_model

    model = get_chat_model().with_structured_output(Suggestions)
    return await model.ainvoke(
        [("system", SYSTEM_PROMPT), ("human", user_prompt(c, activities))]
    )


def get_concern_suggester() -> Suggester:
    """FastAPI dependency；測試換成假的。"""
    return llm_suggest


def validated(raw: object) -> list[Suggestion]:
    """不在清單、重複或不是恰好 3 項時丟 ValueError（ValidationError 也是 ValueError）。"""
    suggestions = Suggestions.model_validate(raw).suggestions
    concerns = {s.concern for s in suggestions}
    if len(suggestions) != COUNT or len(concerns) != COUNT:
        raise ValueError(f"推薦的在意事項要恰好 {COUNT} 項且不重複：{raw!r}")
    return suggestions
