"""提醒信的規則（ADR-0031、規格書 4.9）：不碰資料庫，排程與寄信都依這裡判斷。"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
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
    # 掃描看到文件都齊全的時間；之後又缺文件時清掉，改記 resumed_at
    paused_at: datetime | None = None
    # 文件齊全後又缺文件（取消勾選、刪掉文件）的時間；下一封已經過了預計時間就從這裡重新起算
    resumed_at: datetime | None = None


def next_reminder_at(
    state: ReminderState, *, first_delay: timedelta, interval: timedelta
) -> datetime | None:
    """下一封提醒信的時間；不再寄時回傳 None。

    文件齊全後又缺文件，而且那時已經過了下一封的預計時間：改從又缺文件的時間起算
    （第 1 封等 `first_delay`，第 2、3 封等 `interval`），不馬上補寄。
    """
    if state.closed or state.started_at is None or state.sent_count >= MAX_REMINDERS:
        return None
    if state.sent_count > 0 and state.last_sent_at is not None:
        at, wait = state.last_sent_at + interval, interval
    else:
        at, wait = state.started_at + first_delay, first_delay
    if state.resumed_at is not None and state.resumed_at > at:
        return state.resumed_at + wait
    return at


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
    @property
    def updated_at(self) -> datetime: ...


def missing_uploads[T: TodoLike](todos: Iterable[T]) -> list[T]:
    """還缺文件的待辦，順序不變；清單是空的就該停止寄提醒信。

    使用者自行勾選、沒附文件的待辦（`without_document`）狀態也是 done，所以不算還缺。
    """
    return [
        t for t in todos if t.requires_upload and t.applicable and t.status != "done"
    ]


def sync_round(state: ReminderState, todos: Iterable[TodoLike]) -> ReminderState:
    """依目前的待辦更新這一輪的起點。

    - 還沒開始計時：以最早一筆需上傳而且適用的待辦的建立時間開始第一輪（缺文件起算）。
    - 這一輪還沒寄出任何一封：之後新增的待辦（新增事故、補問多出的文件）都算在這一輪，起點不變，
      第一封照原本的時間寄，信裡一起列出。
    - 這一輪已經寄過信：有在**最後一封寄出之後**才建立的需上傳待辦，就開新的一輪並把計數歸零。
      界線用寄出時間，不用起點：第一封寄出前就併進這一輪的待辦，寄信後不能再被當成新增。
    """
    starts = [t.created_at for t in todos if t.requires_upload and t.applicable]
    if not starts:
        return state
    if state.started_at is None:
        return replace(state, started_at=min(starts), sent_count=0)
    latest = max(starts)
    if (
        state.sent_count == 0
        or state.last_sent_at is None
        or latest <= max(state.started_at, state.last_sent_at)
    ):
        return state
    return ReminderState(
        started_at=latest,
        sent_count=0,
        last_sent_at=state.last_sent_at,
        closed=state.closed,
    )


def track_pause(
    state: ReminderState, todos: Iterable[TodoLike], now: datetime
) -> ReminderState:
    """依目前缺不缺文件，記下這一輪暫停（文件都齊全）與恢復（又缺文件）的時間。

    恢復時間取還缺的待辦裡最早被改動的 `updated_at`（取消勾選或刪掉文件的時間），
    不早於暫停時間；暫停與恢復都發生在兩次掃描之間時看不到，照原本的時間寄。
    """
    if state.started_at is None:
        return state
    missing = missing_uploads(todos)
    if not missing:
        return state if state.paused_at else replace(state, paused_at=now)
    if state.paused_at is None:
        return state
    resumed = min(t.updated_at for t in missing)
    return replace(state, paused_at=None, resumed_at=max(resumed, state.paused_at))
