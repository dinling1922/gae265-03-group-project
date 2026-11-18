"""LINE 訊息的長度限制與截斷（ADR-0033）。比較、待辦指令共用。

LINE 的限制：單則文字 5000 字、快速回覆 label 20 字、快速回覆最多 13 個、一次回覆最多 5 則。
目前指令都只回一則，過長時截斷並提示到網頁看，不分多則（回覆權杖只能用一次，分則也沒有更好看）。
"""

from collections.abc import Sequence

from app.line_bot.client import QuickReply

MAX_TEXT_LENGTH = 5000
MAX_LABEL_LENGTH = 20
MAX_QUICK_REPLIES = 13

# 暫定文案（待設計者確認）
TRUNCATED_NOTICE = "…（內容太長，後面省略了，完整內容請到網頁查看）"


def truncate_label(label: str) -> str:
    """快速回覆 label 超過 20 字時截短（最後一個字換成「…」）。"""
    if len(label) <= MAX_LABEL_LENGTH:
        return label
    return label[: MAX_LABEL_LENGTH - 1] + "…"


def clamp_quick_replies(items: Sequence[QuickReply]) -> tuple[QuickReply, ...]:
    """最多 13 個；label 截短。text 照原樣（它是按下去送出的指令，不能截）。"""
    return tuple(
        QuickReply(truncate_label(q.label), q.text) for q in items[:MAX_QUICK_REPLIES]
    )


def fit_text(text: str, limit: int = MAX_TEXT_LENGTH) -> str:
    """超過 `limit` 字時，在最後一個換行處截斷（不切斷一行），接上提示；沒超過原樣回傳。"""
    if len(text) <= limit:
        return text
    room = limit - len(TRUNCATED_NOTICE) - 1
    cut = text[:room]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return cut.rstrip() + "\n" + TRUNCATED_NOTICE
