"""提醒信規則（ADR-0028、規格書 4.9）：下一封何時寄、還缺哪些文件。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.reminder.rules import ReminderState, missing_uploads, next_reminder_at
from app.models import IncidentTodo

DAY = timedelta(days=1)


@dataclass
class Todo:
    title: str
    requires_upload: bool = True
    applicable: bool = True
    status: str = "pending"


START = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)


def due(state: ReminderState) -> datetime | None:
    return next_reminder_at(state, first_delay=DAY, interval=3 * DAY)


def test_第一封在計時起點後_24_小時() -> None:
    state = ReminderState(started_at=START, sent_count=0, last_sent_at=None)
    assert due(state) == datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


def test_第二封從第一封實際寄出的時間起算_3_天() -> None:
    # 第一封晚了 10 分鐘才寄出（掃描間隔），第二封跟著晚 10 分鐘
    sent = datetime(2026, 7, 12, 9, 10, tzinfo=UTC)
    state = ReminderState(started_at=START, sent_count=1, last_sent_at=sent)
    assert due(state) == datetime(2026, 7, 15, 9, 10, tzinfo=UTC)


def test_寄滿_3_封就不再寄() -> None:
    sent = datetime(2026, 7, 18, 9, 10, tzinfo=UTC)
    state = ReminderState(started_at=START, sent_count=3, last_sent_at=sent)
    assert due(state) is None


def test_沒有在計時就不寄() -> None:
    # 還沒產生待辦，或文件已經備齊（計時起點被清空）
    state = ReminderState(started_at=None, sent_count=1, last_sent_at=START)
    assert due(state) is None


def test_行程結案後不再寄_即使這一輪還沒寄滿() -> None:
    state = ReminderState(
        started_at=START, sent_count=1, last_sent_at=START + DAY, closed=True
    )
    assert due(state) is None


def test_新增需上傳的待辦重新計時_第一封重新從_24_小時算() -> None:
    # 上一輪已寄 2 封；7/20 新狀況新增待辦，計數歸零但 last_sent_at 還是上一輪的
    restarted = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
    state = ReminderState(
        started_at=restarted,
        sent_count=0,
        last_sent_at=datetime(2026, 7, 15, 9, 10, tzinfo=UTC),
    )
    assert due(state) == datetime(2026, 7, 21, 15, 0, tzinfo=UTC)


def test_停擺很久恢復後只補一封_下一封再等_3_天() -> None:
    # 第一封 7/12 寄出後 worker 停了 8 天；7/20 恢復時第二封已過期，補寄後下一封是 7/23
    late = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    first = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
    before = ReminderState(started_at=START, sent_count=1, last_sent_at=first)
    assert due(before) == datetime(
        2026, 7, 15, 9, 0, tzinfo=UTC
    )  # 早已過期，只補這一封

    after = ReminderState(started_at=START, sent_count=2, last_sent_at=late)
    assert due(after) == datetime(2026, 7, 23, 8, 0, tzinfo=UTC)


def test_還缺的只有需上傳而且還沒完成的待辦() -> None:
    proof = Todo("班機取消證明")
    receipt = Todo("住宿收據", status="done")
    call = Todo("通知保險公司", requires_upload=False)
    assert [t.title for t in missing_uploads([proof, receipt, call])] == [
        "班機取消證明"
    ]


def test_標為不適用的待辦不算還缺() -> None:
    # 5 號依最新狀況判斷不適用（#232），送件前檢查也當作沒有這一項
    dropped = Todo("行李延誤證明", applicable=False)
    assert missing_uploads([dropped]) == []


def test_使用者自行勾選而沒附文件的待辦不算還缺() -> None:
    # 規格 4.9：含使用者自行勾選、文件未齊的待辦
    checked = Todo("醫療診斷書", status="done")
    assert missing_uploads([checked]) == []


def test_資料庫的事件待辦可以直接傳進來() -> None:
    todos = [
        IncidentTodo(phase="after_return", title="班機取消證明", requires_upload=True),
        IncidentTodo(
            phase="after_return",
            title="住宿收據",
            requires_upload=True,
            status="done",
            without_document=True,
        ),
    ]
    assert [t.title for t in missing_uploads(todos)] == ["班機取消證明"]
