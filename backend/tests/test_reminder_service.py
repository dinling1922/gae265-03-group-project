"""掃描與寄提醒信（ADR-0031）：連測試資料庫，用假的寄信器記下寄了什麼。"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest
from app.domain.reminder.mailer import OutgoingMail
from app.domain.reminder.rounds import policy_rounds
from app.domain.reminder.service import due_policy_ids, send_reminder
from app.domain.reminder.store import policy_todos
from app.models import (
    Identity,
    Incident,
    IncidentFact,
    IncidentTodo,
    IncidentType,
    Policy,
    ReminderLetter,
    User,
)
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

ADDED = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)  # 待辦建立時間＝這一輪的起點
FIRST_DUE = ADDED + timedelta(days=1)


@dataclass
class FakeMailer:
    sent: list[OutgoingMail] = field(default_factory=list)
    fail: bool = False

    def send(self, mail: OutgoingMail) -> None:
        if self.fail:
            raise OSError("SMTP 連不上")
        self.sent.append(mail)


@dataclass
class Trip:
    policy: Policy
    incident: Incident
    todo: IncidentTodo


async def make_trip(
    session: AsyncSession, *, verified: bool = True, typed: bool = True
) -> Trip:
    user = User(email="traveler@example.com", name="王小明")
    session.add(user)
    await session.flush()
    session.add(
        Identity(
            user_id=user.id,
            provider="google",
            provider_user_id="sub-1",
            email_verified=verified,
        )
    )
    policy = Policy(
        user_id=user.id,
        display_name="進階型",
        plan_id="fubon_advanced",
        destination="日本",
        start_date=date(2026, 7, 5),
        end_date=date(2026, 7, 10),
    )
    session.add(policy)
    await session.flush()
    itype = IncidentType(code="cancel", name="班機取消", follow_up_questions=[])
    session.add(itype)
    await session.flush()
    # 沒帶標題時，事故的 title 是使用者描述的前 50 字
    incident = Incident(
        policy_id=policy.id,
        incident_type_id=itype.id if typed else None,
        title="颱風來了，回台灣的班機被取消。",
        description="颱風來了，回台灣的班機被取消。",
        occurred_at=datetime(2026, 7, 10, 2, 0, tzinfo=UTC),
        # 事故先建立，問完才產生待辦
        created_at=ADDED - timedelta(minutes=10),
    )
    session.add(incident)
    await session.flush()
    todo = IncidentTodo(
        incident_id=incident.id,
        phase="after_return",
        title="航空公司開立的班機取消證明",
        requires_upload=True,
        created_at=ADDED,
    )
    session.add(todo)
    await session.commit()
    return Trip(policy, incident, todo)


async def test_到期而且信箱已驗證就寄一封_計數加一(session: AsyncSession) -> None:
    trip = await make_trip(session)
    mailer = FakeMailer()

    assert await send_reminder(session, trip.policy.id, FIRST_DUE, mailer) is True

    [mail] = mailer.sent
    assert mail.to == "traveler@example.com"
    assert (
        mail.letter.subject
        == "【好險有你 Tripcare】日本 2026/07/05～07/10 還有理賠文件尚未上傳"
    )
    await session.refresh(trip.policy)
    assert trip.policy.reminder_count == 1
    assert trip.policy.last_reminded_at == FIRST_DUE


@pytest.fixture(autouse=True)
def _fixed_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "REMINDER_FIRST_DELAY_SECONDS", 24 * 60 * 60)
    monkeypatch.setattr(settings, "REMINDER_INTERVAL_SECONDS", 3 * 24 * 60 * 60)


async def test_還沒到期就不寄(session: AsyncSession) -> None:
    trip = await make_trip(session)
    mailer = FakeMailer()
    early = FIRST_DUE - timedelta(minutes=1)
    assert await send_reminder(session, trip.policy.id, early, mailer) is False
    assert mailer.sent == []


async def test_行程結案後不寄(session: AsyncSession) -> None:
    trip = await make_trip(session)
    trip.policy.closed_at = ADDED
    session.add(trip.policy)
    await session.commit()
    mailer = FakeMailer()
    assert await send_reminder(session, trip.policy.id, FIRST_DUE, mailer) is False
    assert mailer.sent == []


async def test_文件都備齊就不寄(session: AsyncSession) -> None:
    trip = await make_trip(session)
    trip.todo.status = "done"
    session.add(trip.todo)
    await session.commit()
    mailer = FakeMailer()
    assert await send_reminder(session, trip.policy.id, FIRST_DUE, mailer) is False
    assert mailer.sent == []


async def test_信箱沒有驗證就不寄(session: AsyncSession) -> None:
    trip = await make_trip(session, verified=False)
    mailer = FakeMailer()
    assert await send_reminder(session, trip.policy.id, FIRST_DUE, mailer) is False
    assert mailer.sent == []


async def test_寄信失敗時計數不變_交給重試(session: AsyncSession) -> None:
    trip = await make_trip(session)
    with pytest.raises(OSError):
        await send_reminder(session, trip.policy.id, FIRST_DUE, FakeMailer(fail=True))
    await session.rollback()
    await session.refresh(trip.policy)
    assert trip.policy.reminder_count == 0
    assert trip.policy.last_reminded_at is None


async def test_同一封重複執行只寄一次(session: AsyncSession) -> None:
    # Celery 重複派發：第二個工作看到計數已經加過，下一封還沒到期
    trip = await make_trip(session)
    mailer = FakeMailer()
    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)
    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)
    assert len(mailer.sent) == 1


async def test_掃描挑出到期的保單_並記下這一輪的起點(session: AsyncSession) -> None:
    trip = await make_trip(session)

    assert await due_policy_ids(session, FIRST_DUE - timedelta(minutes=1)) == []
    assert await due_policy_ids(session, FIRST_DUE) == [trip.policy.id]
    await session.refresh(trip.policy)
    assert trip.policy.reminder_started_at == ADDED


async def test_信裡依事故分組_行程共用的文件另列_連到第一個缺件的事故(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://trip.example.com")
    trip = await make_trip(session)
    session.add(
        IncidentTodo(
            policy_id=trip.policy.id,
            phase="after_return",
            title="理賠申請書",
            requires_upload=True,
            created_at=ADDED,
        )
    )
    await session.commit()
    mailer = FakeMailer()

    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)

    text = mailer.sent[0].letter.text
    assert (
        "班機取消 · 2026/07/10\n・航空公司開立的班機取消證明\n\n共同文件\n・理賠申請書\n"
        in text
    )
    assert "寄給富邦產物，" in text
    assert (
        f"前往上傳：https://trip.example.com/trip/{trip.policy.id}"
        f"?incident={trip.incident.id}\n"
    ) in text


async def test_事故用類型名稱_不用使用者的描述(session: AsyncSession) -> None:
    trip = await make_trip(session)
    mailer = FakeMailer()

    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)

    text = mailer.sent[0].letter.text
    assert "班機取消 · 2026/07/10\n" in text
    assert "颱風來了" not in text


async def test_還沒判斷出類型時寫這次事故_日期可用整理出的事故日期(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session, typed=False)
    trip.incident.occurred_at = None
    session.add(trip.incident)
    session.add(
        IncidentFact(
            incident_id=trip.incident.id,
            fact_key="date",
            label="日期",
            value="2026-07-09",
        )
    )
    await session.commit()
    mailer = FakeMailer()

    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)

    text = mailer.sent[0].letter.text
    assert "這次事故 · 2026/07/09\n" in text
    assert "颱風來了" not in text


async def test_掃描不挑信箱沒驗證的保單_免得每次都派發不會寄的工作(
    session: AsyncSession,
) -> None:
    await make_trip(session, verified=False)
    assert await due_policy_ids(session, FIRST_DUE) == []


async def test_只缺共同文件時連到最新一個已確認的事故_不連到還沒確認的(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://trip.example.com")
    trip = await make_trip(session)
    trip.todo.status = "done"
    session.add(trip.todo)
    session.add(
        Incident(
            policy_id=trip.policy.id,
            title="還在問的事故",
            description="還在問的事故",
            confirmation="awaiting",
        )
    )
    session.add(
        IncidentTodo(
            policy_id=trip.policy.id,
            phase="after_return",
            title="理賠申請書",
            requires_upload=True,
            created_at=ADDED,
        )
    )
    await session.commit()
    mailer = FakeMailer()

    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)

    text = mailer.sent[0].letter.text
    assert (
        f"前往上傳：https://trip.example.com/trip/{trip.policy.id}"
        f"?incident={trip.incident.id}\n"
    ) in text


SECOND_DUE = FIRST_DUE + timedelta(days=3)
# 第一封寄出後兩天新增狀況（台灣時間 07/13）
LATER = ADDED + timedelta(days=2)


async def add_incident(session: AsyncSession, trip: Trip) -> Incident:
    """第一封寄出後新增的事故：行李延誤，發生在 07/13。"""
    bag = IncidentType(code="bag_delay", name="行李延誤", follow_up_questions=[])
    session.add(bag)
    await session.flush()
    incident = Incident(
        policy_id=trip.policy.id,
        incident_type_id=bag.id,
        title="行李沒到",
        description="行李沒到",
        occurred_at=datetime(2026, 7, 13, 2, 0, tzinfo=UTC),
        created_at=LATER,
    )
    session.add(incident)
    await session.flush()
    session.add(
        IncidentTodo(
            incident_id=incident.id,
            phase="after_return",
            title="行李延誤證明",
            requires_upload=True,
            created_at=LATER + timedelta(minutes=5),
        )
    )
    await session.commit()
    return incident


async def test_寄出後記下這一封的預計與實際寄出時間_信件的_Date_也用寄出時間(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    mailer = FakeMailer()
    now = FIRST_DUE + timedelta(seconds=12)

    await send_reminder(session, trip.policy.id, now, mailer)

    [record] = (await session.exec(select(ReminderLetter))).all()
    assert (record.round_started_at, record.number) == (ADDED, 1)
    assert (record.planned_at, record.sent_at) == (FIRST_DUE, now)
    assert mailer.sent[0].sent_at == now
    assert mailer.sent[0].in_reply_to is None


async def test_同一輪的第二封主旨相同_接在第一封後面(session: AsyncSession) -> None:
    trip = await make_trip(session)
    mailer = FakeMailer()

    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)
    await send_reminder(session, trip.policy.id, SECOND_DUE, mailer)

    first, second = mailer.sent
    assert second.letter.subject == first.letter.subject
    assert second.in_reply_to == first.message_id


async def test_寄過信後新增事故_新一輪的主旨寫出新增了哪個事故(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    mailer = FakeMailer()
    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)
    await add_incident(session, trip)

    await send_reminder(session, trip.policy.id, LATER + timedelta(days=2), mailer)

    assert mailer.sent[1].letter.subject == (
        "【好險有你 Tripcare】日本 2026/07/05～07/10 還有理賠文件尚未上傳"
        "（07/13 新增事故「行李延誤 · 2026/07/13」重新提醒）"
    )
    assert mailer.sent[1].in_reply_to is None


async def test_寄過信後原本的事故多了文件_主旨寫哪個事故新增狀況(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    mailer = FakeMailer()
    await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)
    session.add(
        IncidentTodo(
            incident_id=trip.incident.id,
            phase="after_return",
            title="住宿收據",
            requires_upload=True,
            created_at=LATER,
        )
    )
    await session.commit()

    await send_reminder(session, trip.policy.id, LATER + timedelta(days=2), mailer)

    assert mailer.sent[1].letter.subject.endswith(
        "（07/13 「班機取消 · 2026/07/10」新增狀況重新提醒）"
    )


async def test_寄送進度_列出寄過信的輪次與目前這一輪_被取代的輪次記下結束時間(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    await send_reminder(session, trip.policy.id, FIRST_DUE, FakeMailer())
    await add_incident(session, trip)
    await session.refresh(trip.policy)

    rounds = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )

    first, current = rounds
    new_start = LATER + timedelta(minutes=5)
    assert (first.started_at, first.ended_at, first.trigger) == (ADDED, new_start, None)
    assert [slot.sent_at for slot in first.letters] == [FIRST_DUE, None, None]
    assert first.end_reason == "new_round"
    assert current.started_at == new_start and current.ended_at is None
    assert current.end_reason is None
    assert current.trigger is not None
    assert (current.trigger.incident, current.trigger.kind) == (
        "行李延誤 · 2026/07/13",
        "new_incident",
    )
    assert [slot.planned_at for slot in current.letters] == [
        new_start + timedelta(days=1),
        new_start + timedelta(days=4),
        new_start + timedelta(days=7),
    ]


async def test_第一封寄出前新增事故_算在初次理賠待辦_照原本的時間一起寄(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    assert await due_policy_ids(session, ADDED + timedelta(hours=1)) == []
    await add_incident(session, trip)  # LATER 在第一封（FIRST_DUE）之後，但第一封還沒寄
    await session.refresh(trip.policy)

    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )
    assert (only.started_at, only.trigger, only.end_reason) == (ADDED, None, None)

    mailer = FakeMailer()
    assert await send_reminder(
        session, trip.policy.id, LATER + timedelta(hours=1), mailer
    )
    letter = mailer.sent[0].letter
    # 同一輪：主旨不加重新提醒的原因，兩個事故缺的文件一起列出
    assert letter.subject.endswith("還有理賠文件尚未上傳")
    assert "行李延誤證明" in letter.text and "班機取消" in letter.text


async def test_還沒開始計時就有兩個事故_以最早的待辦開始第一輪(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    await add_incident(session, trip)

    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )

    assert (only.started_at, only.trigger) == (ADDED, None)


async def test_寄送進度_文件都齊全後_最後一輪標成已結束(session: AsyncSession) -> None:
    trip = await make_trip(session)
    await send_reminder(session, trip.policy.id, FIRST_DUE, FakeMailer())
    trip.todo.status = "done"
    session.add(trip.todo)
    await session.commit()
    await session.refresh(trip.todo)

    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )

    assert (only.ended_at, only.end_reason) == (trip.todo.updated_at, "completed")
    assert [slot.sent_at for slot in only.letters] == [FIRST_DUE, None, None]


async def test_寄送進度_行程結案後_最後一輪標成已結束(session: AsyncSession) -> None:
    trip = await make_trip(session)
    closed = FIRST_DUE + timedelta(hours=1)
    trip.policy.closed_at = closed
    session.add(trip.policy)
    await session.commit()

    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )

    assert (only.ended_at, only.end_reason) == (closed, "closed")


async def mark(
    session: AsyncSession,
    todo: IncidentTodo,
    *,
    done: bool,
    at: datetime | None = None,
) -> None:
    """使用者勾選（未附文件）或取消勾選需上傳的待辦；`at` 是操作時間（寫進 updated_at）。"""
    todo.status = "done" if done else "pending"
    todo.without_document = done
    if at is not None:
        todo.updated_at = at
    session.add(todo)
    await session.commit()
    await session.refresh(todo)


async def test_第一封寄出前待辦都勾選_不寄信_這一輪標成已結束(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    await mark(session, trip.todo, done=True)
    mailer = FakeMailer()

    assert await send_reminder(session, trip.policy.id, FIRST_DUE, mailer) is False

    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )
    assert mailer.sent == []
    assert (only.ended_at, only.end_reason) == (trip.todo.updated_at, "completed")
    assert [slot.sent_at for slot in only.letters] == [None, None, None]


async def test_第一封寄出前勾選後又取消勾選_算還缺文件_照原本的時間寄第一封(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    await mark(session, trip.todo, done=True)
    await mark(session, trip.todo, done=False)
    mailer = FakeMailer()

    assert await send_reminder(session, trip.policy.id, FIRST_DUE, mailer) is True

    await session.refresh(trip.policy)
    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )
    assert len(mailer.sent) == 1
    assert only.started_at == ADDED  # 取消勾選不重新計時
    assert (only.ended_at, only.end_reason) == (None, None)
    assert [slot.sent_at for slot in only.letters] == [FIRST_DUE, None, None]


async def test_過了第一封的時間才取消勾選_第一封改從取消勾選起算(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    await mark(session, trip.todo, done=True, at=ADDED + timedelta(hours=1))
    # 掃描看到文件都齊全，記下暫停
    assert await due_policy_ids(session, ADDED + timedelta(hours=2)) == []
    unchecked = FIRST_DUE + timedelta(hours=5)
    await mark(session, trip.todo, done=False, at=unchecked)
    mailer = FakeMailer()

    # 原本第一封的時間已經過了，但不馬上補寄
    assert await due_policy_ids(session, unchecked + timedelta(minutes=10)) == []
    assert (
        await send_reminder(
            session, trip.policy.id, unchecked + timedelta(minutes=10), mailer
        )
        is False
    )
    await session.refresh(trip.policy)
    assert trip.policy.reminder_resumed_at == unchecked
    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )
    assert only.end_reason is None
    assert only.letters[0].planned_at == unchecked + timedelta(days=1)

    # 取消勾選滿 24 小時才寄
    resumed_due = unchecked + timedelta(days=1)
    assert await due_policy_ids(session, resumed_due) == [trip.policy.id]
    assert await send_reminder(session, trip.policy.id, resumed_due, mailer) is True
    letter = (await session.exec(select(ReminderLetter))).one()
    assert (letter.planned_at, letter.sent_at) == (resumed_due, resumed_due)


async def test_第一封寄出前新增的事故_寄信後不會變成新的一輪_之後新增的才是(
    session: AsyncSession,
) -> None:
    trip = await make_trip(session)
    # 第一封寄出前新增的事故（生病或受傷就醫）：併進初次理賠待辦
    sick = IncidentType(code="sick", name="生病或受傷就醫", follow_up_questions=[])
    session.add(sick)
    await session.flush()
    merged_at = ADDED + timedelta(minutes=1)
    gastro = Incident(
        policy_id=trip.policy.id,
        incident_type_id=sick.id,
        title="腸胃炎",
        description="腸胃炎",
        occurred_at=datetime(2026, 7, 11, 2, 0, tzinfo=UTC),
        created_at=merged_at,
    )
    session.add(gastro)
    await session.flush()
    session.add(
        IncidentTodo(
            incident_id=gastro.id,
            phase="after_return",
            title="醫療費用收據",
            requires_upload=True,
            created_at=merged_at,
        )
    )
    await session.commit()
    mailer = FakeMailer()
    assert await send_reminder(session, trip.policy.id, FIRST_DUE, mailer)

    # 寄出後的掃描：還是同一輪
    assert await due_policy_ids(session, FIRST_DUE + timedelta(hours=1)) == []
    await session.refresh(trip.policy)
    [only] = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )
    assert (only.started_at, only.end_reason) == (ADDED, None)
    assert [slot.sent_at for slot in only.letters] == [FIRST_DUE, None, None]

    # 寄出後才新增的事故：開新的一輪，原因是行李延誤，不是之前併進來的腸胃炎
    await add_incident(session, trip)
    await session.refresh(trip.policy)
    first, current = await policy_rounds(
        session, trip.policy, await policy_todos(session, trip.policy.id)
    )
    assert first.started_at == ADDED and first.end_reason == "new_round"
    assert current.trigger is not None
    assert (current.trigger.incident, current.trigger.kind) == (
        "行李延誤 · 2026/07/13",
        "new_incident",
    )
