"""提醒信規則（ADR-0031、規格書 4.9）：下一封何時寄、還缺哪些文件。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.reminder.rules import (
    ReminderState,
    missing_uploads,
    next_reminder_at,
    sync_round,
    track_pause,
)
from app.models import IncidentTodo

DAY = timedelta(days=1)


@dataclass
class Todo:
    title: str
    requires_upload: bool = True
    applicable: bool = True
    status: str = "pending"
    created_at: datetime = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    updated_at: datetime = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)


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


def test_第一次出現需上傳的待辦_以它的建立時間開始這一輪() -> None:
    fresh = ReminderState(started_at=None, sent_count=0, last_sent_at=None)
    todos = [Todo("班機取消證明", created_at=START)]
    assert sync_round(fresh, todos) == ReminderState(
        started_at=START, sent_count=0, last_sent_at=None
    )


def test_沒有新的待辦_這一輪照舊_已寄的封數不變() -> None:
    state = ReminderState(started_at=START, sent_count=2, last_sent_at=START + 4 * DAY)
    todos = [Todo("班機取消證明", created_at=START), Todo("住宿收據", created_at=START)]
    assert sync_round(state, todos) == state


def test_新狀況新增需上傳的待辦_開新的一輪並歸零() -> None:
    sent = datetime(2026, 7, 15, 9, 10, tzinfo=UTC)
    state = ReminderState(started_at=START, sent_count=2, last_sent_at=sent)
    added = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
    todos = [
        Todo("班機取消證明", created_at=START),
        Todo("行李延誤證明", created_at=added),
    ]
    assert sync_round(state, todos) == ReminderState(
        started_at=added, sent_count=0, last_sent_at=sent
    )


def test_第一次掃描時已有多個事故的待辦_以最早的建立時間開始() -> None:
    fresh = ReminderState(started_at=None, sent_count=0, last_sent_at=None)
    todos = [
        Todo("行李延誤證明", created_at=START + 60 * timedelta(seconds=1)),
        Todo("班機取消證明", created_at=START),
    ]
    assert sync_round(fresh, todos).started_at == START


def test_第一封寄出前新增事故_算在同一輪_起點不變() -> None:
    state = ReminderState(started_at=START, sent_count=0, last_sent_at=None)
    todos = [
        Todo("班機取消證明", created_at=START),
        Todo("行李延誤證明", created_at=START + 20 * 60 * 60 * timedelta(seconds=1)),
    ]
    assert sync_round(state, todos) == state
    assert due(state) == START + DAY


def test_第一封寄出前併進來的待辦_寄信後不算新增_不開新的一輪() -> None:
    merged = START + 20 * 60 * 60 * timedelta(seconds=1)  # 第一封寄出前新增的事故
    first = START + DAY
    state = ReminderState(started_at=START, sent_count=1, last_sent_at=first)
    todos = [
        Todo("班機取消證明", created_at=START),
        Todo("醫療費用收據", created_at=merged),
    ]
    assert sync_round(state, todos) == state


def test_第一封寄出後才新增的待辦_開新的一輪() -> None:
    first = START + DAY
    added = first + 60 * timedelta(seconds=1)
    state = ReminderState(started_at=START, sent_count=1, last_sent_at=first)
    todos = [
        Todo("班機取消證明", created_at=START),
        Todo("行李延誤證明", created_at=added),
    ]
    assert sync_round(state, todos).started_at == added


def test_沒有需上傳的待辦就不開始計時() -> None:
    fresh = ReminderState(started_at=None, sent_count=0, last_sent_at=None)
    todos = [Todo("通知保險公司", requires_upload=False, created_at=START)]
    assert sync_round(fresh, todos) == fresh


def test_新增的待辦不需上傳或不適用_不算新的一輪() -> None:
    state = ReminderState(started_at=START, sent_count=1, last_sent_at=START + DAY)
    later = START + 5 * DAY
    todos = [
        Todo("班機取消證明", created_at=START),
        Todo("通知保險公司", requires_upload=False, created_at=later),
        Todo("行李延誤證明", applicable=False, created_at=later),
    ]
    assert sync_round(state, todos) == state


def test_文件都齊全_掃描記下暫停時間() -> None:
    state = ReminderState(started_at=START, sent_count=0, last_sent_at=None)
    scan = START + 2 * 60 * 60 * timedelta(seconds=1)

    paused = track_pause(state, [Todo("住宿收據", status="done")], scan)

    assert paused.paused_at == scan
    # 已經暫停就不再更新
    assert track_pause(paused, [Todo("住宿收據", status="done")], scan + DAY) == paused


def test_過了第一封的時間才取消勾選_第一封從取消勾選起算_24_小時() -> None:
    paused_at = START + 2 * 60 * 60 * timedelta(seconds=1)
    unchecked = START + 2 * DAY
    state = ReminderState(
        started_at=START, sent_count=0, last_sent_at=None, paused_at=paused_at
    )

    resumed = track_pause(state, [Todo("住宿收據", updated_at=unchecked)], unchecked)

    assert (resumed.paused_at, resumed.resumed_at) == (None, unchecked)
    assert due(resumed) == unchecked + DAY


def test_第一封的時間還沒到就取消勾選_照原本的時間寄() -> None:
    unchecked = START + 2 * 60 * 60 * timedelta(seconds=1)
    state = ReminderState(
        started_at=START, sent_count=0, last_sent_at=None, paused_at=START
    )

    resumed = track_pause(state, [Todo("住宿收據", updated_at=unchecked)], unchecked)

    assert due(resumed) == START + DAY


def test_寄過一封後_過了第二封的時間才取消勾選_第二封從取消勾選起算_3_天() -> None:
    first = START + DAY
    unchecked = first + 5 * DAY
    state = ReminderState(
        started_at=START,
        sent_count=1,
        last_sent_at=first,
        resumed_at=unchecked,
    )

    assert due(state) == unchecked + 3 * DAY


def test_沒在暫停時缺文件_不記恢復時間() -> None:
    state = ReminderState(started_at=START, sent_count=0, last_sent_at=None)

    assert track_pause(state, [Todo("住宿收據")], START + DAY) == state


def test_新的一輪清掉暫停與恢復時間() -> None:
    state = ReminderState(
        started_at=START,
        sent_count=1,
        last_sent_at=START + DAY,
        resumed_at=START + 5 * DAY,
    )
    later = START + 6 * DAY

    restarted = sync_round(state, [Todo("行李延誤證明", created_at=later)])

    assert (restarted.started_at, restarted.resumed_at) == (later, None)
