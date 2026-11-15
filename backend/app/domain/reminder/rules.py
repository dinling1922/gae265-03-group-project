"""提醒信的規則（ADR-0031、規格書 4.9）：不碰資料庫，排程與寄信都依這裡判斷。"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

# 每一輪最多寄幾封（資料庫的 reminder_count 也限制在 0～3）
MAX_REMINDERS = 3


@dataclass(frozen=True)
class ReminderState:
    """一張保單這一輪提醒信的計時狀態（對應 policy 的 reminder_* 欄位）。"""

    started_at: datetime | None
    sent_count: int
    last_sent_at: datetime | None
    closed: bool = False


def next_reminder_at(
    state: ReminderState, *, first_delay: timedelta, interval: timedelta
) -> datetime | None:
    """下一封提醒信的時間；不再寄時回傳 None。"""
    if state.closed or state.started_at is None or state.sent_count >= MAX_REMINDERS:
        return None
    if state.sent_count > 0 and state.last_sent_at is not None:
        return state.last_sent_at + interval
    return state.started_at + first_delay


class TodoLike(Protocol):
    """判斷還缺不缺文件需要的待辦欄位（`IncidentTodo` 就符合）。"""

    @property
    def requires_upload(self) -> bool: ...
    @property
    def applicable(self) -> bool: ...
    @property
    def status(self) -> str: ...
    @property
    def created_at(self) -> datetime: ...


def missing_uploads[T: TodoLike](todos: Iterable[T]) -> list[T]:
    """還缺文件的待辦，順序不變；清單是空的就該停止寄提醒信。

    使用者自行勾選、沒附文件的待辦（`without_document`）狀態也是 done，所以不算還缺。
    """
    return [
        t for t in todos if t.requires_upload and t.applicable and t.status != "done"
    ]


def sync_round(state: ReminderState, todos: Iterable[TodoLike]) -> ReminderState:
    """依目前的待辦更新這一輪的起點：最新一筆需上傳而且適用的待辦比起點晚，就開新的一輪。"""
    starts = [t.created_at for t in todos if t.requires_upload and t.applicable]
    latest = max(starts, default=None)
    if latest is None or (state.started_at is not None and latest <= state.started_at):
        return state
    return ReminderState(
        started_at=latest,
        sent_count=0,
        last_sent_at=state.last_sent_at,
        closed=state.closed,
    )
