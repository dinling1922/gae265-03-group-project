"""提醒信的輪次（ADR-0031）：每一輪的起點、因為哪個事故開始、每封的預計與實際寄出時間。

信件主旨（是不是重新提醒、因為哪個事故）與行程頁的寄送進度都從這裡算，兩邊說法一致。
這裡只讀資料庫，不寫入。
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.domain.reminder.rules import (
    MAX_REMINDERS,
    ReminderState,
    TodoLike,
    missing_uploads,
    sync_round,
    track_pause,
)
from app.models import (
    Incident,
    IncidentFact,
    IncidentTodo,
    IncidentType,
    Policy,
    ReminderLetter,
)

TAIPEI = ZoneInfo("Asia/Taipei")
# 還沒判斷出事故類型時的名稱；和理賠頁一致（UploadBox、TodoList）
UNTYPED_INCIDENT_LABEL = "這次事故"
_FACT_DATE = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$")

TriggerKind = Literal["new_incident", "new_situation"]
# 一輪為什麼結束：有新的一輪、需上傳的文件都齊全（含自行勾選）、行程結案
EndReason = Literal["new_round", "completed", "closed"]


@dataclass(frozen=True)
class RoundTrigger:
    """這一輪因為哪個事故開始：`new_incident` 新增了這個事故，`new_situation` 原本的事故有新狀況。"""

    incident: str
    kind: TriggerKind


@dataclass(frozen=True)
class LetterSlot:
    planned_at: datetime
    sent_at: datetime | None


@dataclass(frozen=True)
class Round:
    started_at: datetime
    ended_at: datetime | None
    end_reason: EndReason | None
    trigger: RoundTrigger | None
    letters: list[LetterSlot]


def first_delay() -> timedelta:
    return timedelta(seconds=settings.REMINDER_FIRST_DELAY_SECONDS)


def interval() -> timedelta:
    return timedelta(seconds=settings.REMINDER_INTERVAL_SECONDS)


def planned_times(
    started_at: datetime,
    sent: list[datetime | None],
    resumed_at: datetime | None = None,
) -> list[datetime]:
    """每封的預計時間：第 1 封是起點加 24 小時，之後是上一封（寄了用實際時間）加 3 天。

    `resumed_at` 是文件齊全後又缺文件的時間：還沒寄的第一封如果那時已經過了預計時間，
    改從 `resumed_at` 起算（同 `rules.next_reminder_at`）。
    """
    times: list[datetime] = []
    at = started_at + first_delay()
    resumed = resumed_at
    for number in range(MAX_REMINDERS):
        previous = sent[number] if number < len(sent) else None
        if previous is None and resumed is not None:
            if resumed > at:
                at = resumed + (first_delay() if number == 0 else interval())
            resumed = None
        times.append(at)
        at = (previous or at) + interval()
    return times


def trigger_text(trigger: RoundTrigger | None) -> str:
    """新的一輪因為什麼開始；和行程頁的說法一致（ReminderProgress 的 triggerText）。"""
    if trigger is None:
        return "新增狀況"
    if trigger.kind == "new_incident":
        return f"新增事故「{trigger.incident}」"
    return f"「{trigger.incident}」新增狀況"


def taipei_day(at: datetime) -> str:
    """台灣時間的「11/16」。"""
    return f"{at.astimezone(TAIPEI):%m/%d}"


def _incident_date(incident: Incident, fact_date: str | None) -> str | None:
    """發生日：先用 occurred_at，沒有就用整理出的事故日期（和理賠頁的 `dateOf` 一致）。"""
    if incident.occurred_at is not None:
        return f"{incident.occurred_at.astimezone(TAIPEI):%Y/%m/%d}"
    if fact_date and _FACT_DATE.match(fact_date):
        return fact_date.replace("-", "/")
    return None


async def incident_labels(
    session: AsyncSession, policy_id: uuid.UUID
) -> list[tuple[Incident, str]]:
    """已確認的事故（依建立順序）與它在信裡的名稱「事故類型 · 發生日」。

    用事故類型名稱，不用 title（沒帶時是使用者描述的前 50 字）；還沒確認的事故（#345）不算。
    """
    rows = (
        await session.exec(
            select(Incident, IncidentType.name)
            .join(
                IncidentType,
                col(Incident.incident_type_id) == col(IncidentType.id),
                isouter=True,
            )
            .where(
                Incident.policy_id == policy_id, Incident.confirmation == "confirmed"
            )
            .order_by(col(Incident.created_at))
        )
    ).all()
    fact_dates = {
        f.incident_id: f.value
        for f in (
            await session.exec(
                select(IncidentFact).where(
                    col(IncidentFact.incident_id).in_([i.id for i, _ in rows]),
                    IncidentFact.fact_key == "date",
                )
            )
        ).all()
    }
    labelled: list[tuple[Incident, str]] = []
    for incident, type_name in rows:
        label = type_name or UNTYPED_INCIDENT_LABEL
        day = _incident_date(incident, fact_dates.get(incident.id))
        labelled.append((incident, f"{label} · {day}" if day else label))
    return labelled


def round_trigger(
    todos: list[IncidentTodo],
    labels: list[tuple[Incident, str]],
    started_at: datetime,
    since: datetime,
) -> RoundTrigger | None:
    """這一輪因為哪個事故開始：看上一輪最後一封寄出（`since`）之後新出現的需上傳待辦。

    其中有事故在那之後才建立，就是新增事故；否則是最新那筆待辦的事故有新狀況。
    第一封寄出前就併進上一輪的事故不算。只有共同文件（不屬於事故）時回 None。
    """
    by_id = {incident.id: (incident, label) for incident, label in labels}
    fresh = sorted(
        (
            t
            for t in todos
            if t.requires_upload
            and t.applicable
            and since < t.created_at <= started_at
            and t.incident_id in by_id
        ),
        key=lambda t: t.created_at,
    )
    if not fresh:
        return None
    new = [
        by_id[t.incident_id]
        for t in fresh
        if t.incident_id is not None and by_id[t.incident_id][0].created_at > since
    ]
    if new:
        return RoundTrigger(incident=new[-1][1], kind="new_incident")
    latest = fresh[-1].incident_id
    assert latest is not None
    return RoundTrigger(incident=by_id[latest][1], kind="new_situation")


def current_state(
    policy: Policy, todos: Iterable[TodoLike], now: datetime | None = None
) -> ReminderState:
    """依現在的待辦算出這一輪，含暫停與恢復時間（不寫回資料庫；掃描時才寫）。

    取消勾選後、下一次掃描前打開行程頁，也會看到從取消勾選起算的預計時間。
    """
    todos = list(todos)
    state = ReminderState(
        started_at=policy.reminder_started_at,
        sent_count=policy.reminder_count,
        last_sent_at=policy.last_reminded_at,
        closed=policy.closed_at is not None,
        paused_at=policy.reminder_paused_at,
        resumed_at=policy.reminder_resumed_at,
    )
    return track_pause(sync_round(state, todos), todos, now or datetime.now(UTC))


async def sent_letters(
    session: AsyncSession, policy_id: uuid.UUID
) -> list[ReminderLetter]:
    return list(
        (
            await session.exec(
                select(ReminderLetter)
                .where(ReminderLetter.policy_id == policy_id)
                .order_by(
                    col(ReminderLetter.round_started_at), col(ReminderLetter.number)
                )
            )
        ).all()
    )


def previous_round_last_sent(
    letters: list[ReminderLetter], started_at: datetime
) -> datetime | None:
    """這一輪之前最後寄出的那一封的時間；沒有就是第一輪。

    新的一輪只在上一輪寄過信之後、有新的需上傳待辦才開始（`rules.sync_round`），
    所以前面的輪次都看得到寄過的信，這個時間也就是判斷「新增了什麼」的界線。
    """
    earlier = [
        letter.sent_at for letter in letters if letter.round_started_at < started_at
    ]
    return max(earlier, default=None)


def round_finish(
    policy: Policy, todos: Iterable[IncidentTodo]
) -> tuple[datetime, EndReason] | None:
    """目前這一輪是否已經不再寄：行程結案，或需上傳的文件都齊全（時間取最後完成的待辦）。"""
    if policy.closed_at is not None:
        return policy.closed_at, "closed"
    uploads = [t for t in todos if t.requires_upload and t.applicable]
    if not uploads or missing_uploads(uploads):
        return None
    return max(t.updated_at for t in uploads), "completed"


def build_rounds(
    letters: list[ReminderLetter],
    current_started_at: datetime | None,
    triggers: dict[datetime, RoundTrigger | None],
    finish: tuple[datetime, EndReason] | None = None,
    resumed_at: datetime | None = None,
) -> list[Round]:
    """每一輪，舊的在前：寄過信的輪次，加上目前這一輪。

    新的一輪只在上一輪寄過信之後才開始，第一封寄出前新增的事故或狀況都算在同一輪（`rules.sync_round`）。
    `finish` 是最後一輪結束的時間與原因（`round_finish`）；None 表示還在進行。
    `resumed_at` 只套用在目前這一輪（`planned_times`）。
    """
    sent: dict[datetime, dict[int, datetime]] = defaultdict(dict)
    for letter in letters:
        sent[letter.round_started_at][letter.number] = letter.sent_at
    starts = sorted(set(sent) | ({current_started_at} if current_started_at else set()))
    rounds: list[Round] = []
    for i, start in enumerate(starts):
        actual = [sent[start].get(n) for n in range(1, MAX_REMINDERS + 1)]
        last = i + 1 == len(starts)
        current = last and start == current_started_at
        planned = planned_times(start, actual, resumed_at if current else None)
        ended_at, end_reason = (
            (starts[i + 1], "new_round") if not last else (finish or (None, None))
        )
        rounds.append(
            Round(
                started_at=start,
                ended_at=ended_at,
                end_reason=end_reason,
                trigger=triggers.get(start) if i > 0 else None,
                letters=[
                    LetterSlot(planned_at=p, sent_at=a)
                    for p, a in zip(planned, actual, strict=True)
                ],
            )
        )
    return rounds


async def policy_rounds(
    session: AsyncSession, policy: Policy, todos: list[IncidentTodo]
) -> list[Round]:
    """行程頁的寄送進度：寄過信的每一輪加上目前這一輪。`todos` 用 `store.policy_todos` 的結果。"""
    letters = await sent_letters(session, policy.id)
    state = current_state(policy, todos)
    current = state.started_at
    starts = sorted(
        {letter.round_started_at for letter in letters}
        | ({current} if current else set())
    )
    labels = await incident_labels(session, policy.id) if len(starts) > 1 else []
    triggers: dict[datetime, RoundTrigger | None] = {}
    for start in starts[1:]:
        since = previous_round_last_sent(letters, start)
        triggers[start] = (
            round_trigger(todos, labels, start, since) if since is not None else None
        )
    return build_rounds(
        letters,
        current,
        triggers,
        round_finish(policy, todos),
        state.resumed_at,
    )
