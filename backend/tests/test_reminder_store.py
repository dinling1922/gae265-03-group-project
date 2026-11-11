"""提醒信從資料庫讀的資料（ADR-0028）：一張保單的所有待辦。"""

from app.domain.reminder.rules import ReminderState, sync_round
from app.domain.reminder.store import policy_todos
from app.models import Incident, IncidentTodo, Policy, User
from sqlmodel.ext.asyncio.session import AsyncSession


async def make_policy(session: AsyncSession, email: str) -> Policy:
    user = User(email=email)
    session.add(user)
    await session.flush()
    policy = Policy(user_id=user.id, display_name="進階型", plan_id="fubon_advanced")
    session.add(policy)
    await session.flush()
    return policy


async def test_保單的待辦包含每個事件的待辦與整個行程共用的理賠申請書(
    session: AsyncSession,
) -> None:
    policy = await make_policy(session, "a@example.com")
    other = await make_policy(session, "b@example.com")
    flight = Incident(policy_id=policy.id, title="班機取消", description="取消")
    bag = Incident(policy_id=policy.id, title="行李延誤", description="延誤")
    elsewhere = Incident(policy_id=other.id, title="別人的", description="別人的")
    session.add_all([flight, bag, elsewhere])
    await session.flush()
    session.add_all(
        [
            IncidentTodo(incident_id=flight.id, phase="after_return", title="取消證明"),
            IncidentTodo(incident_id=bag.id, phase="after_return", title="延誤證明"),
            IncidentTodo(policy_id=policy.id, phase="after_return", title="理賠申請書"),
            IncidentTodo(
                incident_id=elsewhere.id, phase="after_return", title="別人的"
            ),
            IncidentTodo(
                policy_id=other.id, phase="after_return", title="別人的申請書"
            ),
        ]
    )
    await session.commit()

    titles = {t.title for t in await policy_todos(session, policy.id)}
    assert titles == {"取消證明", "延誤證明", "理賠申請書"}


async def test_資料庫的待辦可以直接算出這一輪的起點(session: AsyncSession) -> None:
    policy = await make_policy(session, "a@example.com")
    incident = Incident(policy_id=policy.id, title="班機取消", description="取消")
    session.add(incident)
    await session.flush()
    proof = IncidentTodo(
        incident_id=incident.id,
        phase="after_return",
        title="取消證明",
        requires_upload=True,
    )
    session.add(proof)
    await session.commit()

    fresh = ReminderState(started_at=None, sent_count=0, last_sent_at=None)
    state = sync_round(fresh, await policy_todos(session, policy.id))
    assert state.started_at == proof.created_at
