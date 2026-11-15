"""掃描與寄提醒信（ADR-0031）：連測試資料庫，用假的寄信器記下寄了什麼。"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest
from app.domain.reminder.mailer import OutgoingMail
from app.domain.reminder.service import due_policy_ids, send_reminder
from app.models import (
    Identity,
    Incident,
    IncidentFact,
    IncidentTodo,
    IncidentType,
    Policy,
    User,
)
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
    assert mail.letter.subject == "【好險有你】日本 2026/07/05～07/10 還缺 1 份理賠文件"
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
