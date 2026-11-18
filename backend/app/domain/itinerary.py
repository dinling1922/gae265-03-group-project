"""旅遊日程：日程活動的分類與時段範圍（ADR-0031）。"""

from enum import StrEnum

FIRST_HOUR = 7
LAST_HOUR = 22


class ActivityCategory(StrEnum):
    """宣告順序就是畫面上的順序；前端的分類標籤與圖示照這組代碼。"""

    EAT = "eat"
    EXPLORE = "explore"
    CULTURE = "culture"
    COFFEE = "coffee"
    REST = "rest"
    STAY = "stay"
