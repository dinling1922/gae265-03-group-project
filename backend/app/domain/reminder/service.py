"""掃描到期的保單並寄提醒信（ADR-0028）。規則在 `rules.py`，信件內容在 `letter.py`。"""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.domain.plans import get_plan
from app.domain.reminder.letter import (
    LetterInput,
    MissingGroup,
    build_letter,
    message_id,
    trip_name,
)
from app.domain.reminder.mailer import Mailer, OutgoingMail
from app.domain.reminder.rules import (
    ReminderState,
    missing_uploads,
    next_reminder_at,
    sync_round,
)
from app.domain.reminder.store import policy_todos
from app.models import Identity, Incident, IncidentTodo, Policy, User

TAIPEI = ZoneInfo("Asia/Taipei")
# 只缺整個行程共用的文件（理賠申請書）時，信裡這一組的標題
TRIP_GROUP_LABEL = "整個行程"


def _state(policy: Policy) -> ReminderState:
    return ReminderState(
        started_at=policy.reminder_started_at,
        sent_count=policy.reminder_count,
        last_sent_at=policy.last_reminded_at,
        closed=policy.closed_at is not None,
    )


def _apply(policy: Policy, state: ReminderState) -> None:
    policy.reminder_started_at = state.started_at
    policy.reminder_count = state.sent_count


def _next_at(state: ReminderState) -> datetime | None:
    return next_reminder_at(
        state,
        first_delay=timedelta(seconds=settings.REMINDER_FIRST_DELAY_SECONDS),
        interval=timedelta(seconds=settings.REMINDER_INTERVAL_SECONDS),
    )


async def _verified_email(session: AsyncSession, user: User) -> str | None:
    """只寄給提供者驗證過的信箱；開發登入的帳號沒有驗證，一律不寄。"""
    verified = (
        await session.exec(
            select(Identity).where(
                Identity.user_id == user.id, col(Identity.email_verified).is_(True)
            )
        )
    ).first()
    return user.email if verified else None


async def _letter_input(
    session: AsyncSession, policy: Policy, user: User, missing: list[IncidentTodo]
) -> LetterInput:
    incidents = (
        await session.exec(
            select(Incident)
            .where(Incident.policy_id == policy.id)
            .order_by(col(Incident.created_at))
        )
    ).all()
    groups: list[MissingGroup] = []
    first_incident: uuid.UUID | None = None
    for incident in incidents:
        todos = sorted(
            (t for t in missing if t.incident_id == incident.id),
            key=lambda t: t.sort_order,
        )
        if not todos:
            continue
        first_incident = first_incident or incident.id
        label = incident.title
        if incident.occurred_at is not None:
            label += f" · {incident.occurred_at.astimezone(TAIPEI):%Y/%m/%d}"
        groups.append(MissingGroup(label=label, documents=[t.title for t in todos]))
    trip_todos = [t for t in missing if t.incident_id is None]
    if trip_todos:
        groups.append(
            MissingGroup(
                label=TRIP_GROUP_LABEL, documents=[t.title for t in trip_todos]
            )
        )
    # 只缺理賠申請書時，連到這張保單最新的事故
    target = first_incident or (incidents[-1].id if incidents else None)
    base = (settings.PUBLIC_BASE_URL or "http://localhost:5173").rstrip("/")
    plan = get_plan(policy.plan_id)
    return LetterInput(
        user_name=user.name,
        trip_name=trip_name(policy.destination, policy.start_date, policy.end_date),
        groups=groups,
        company=(plan.company if plan and plan.company else "保險公司"),
        upload_url=f"{base}/incident/{target}" if target else base,
    )


async def send_reminder(
    session: AsyncSession, policy_id: uuid.UUID, now: datetime, mailer: Mailer
) -> bool:
    """鎖住保單、重新確認到期後寄一封；寄出成功才把計數加 1。回傳有沒有寄。

    寄信失敗時丟出例外、交易不提交，計數不變，由 Celery 重試。
    """
    policy = (
        await session.exec(
            select(Policy).where(Policy.id == policy_id).with_for_update()
        )
    ).first()
    if policy is None:
        return False
    todos = await policy_todos(session, policy.id)
    state = sync_round(_state(policy), todos)
    _apply(policy, state)
    missing = missing_uploads(todos)
    at = _next_at(state)
    user = await session.get(User, policy.user_id)
    to = await _verified_email(session, user) if user else None
    if not missing or at is None or at > now or user is None or to is None:
        await session.commit()
        return False

    assert state.started_at is not None
    letter = build_letter(await _letter_input(session, policy, user, missing))
    number = state.sent_count + 1
    mailer.send(
        OutgoingMail(
            to=to,
            letter=letter,
            message_id=message_id(
                policy.id, state.started_at, number, settings.MAIL_FROM
            ),
        )
    )
    policy.reminder_count = number
    policy.last_reminded_at = now
    session.add(policy)
    await session.commit()
    return True


async def due_policy_ids(session: AsyncSession, now: datetime) -> list[uuid.UUID]:
    """未結案的保單中，這一輪下一封已到期、而且還缺文件的；順便更新每張保單這一輪的起點。

    這裡只挑出來，不鎖也不寄：寄信工作會鎖住保單再確認一次。
    """
    # 信箱沒驗證的使用者不會收到信（開發登入的帳號），掃描時就排除，不派發寄不出去的工作
    verified_users = select(Identity.user_id).where(
        col(Identity.email_verified).is_(True)
    )
    policies = (
        await session.exec(
            select(Policy).where(
                col(Policy.closed_at).is_(None),
                col(Policy.user_id).in_(verified_users),
            )
        )
    ).all()
    due: list[uuid.UUID] = []
    for policy in policies:
        todos = await policy_todos(session, policy.id)
        state = sync_round(_state(policy), todos)
        if state != _state(policy):
            _apply(policy, state)
            session.add(policy)
        at = _next_at(state)
        if missing_uploads(todos) and at is not None and at <= now:
            due.append(policy.id)
    await session.commit()
    return due
