"""提醒信從資料庫讀的資料（ADR-0031）；規則本身在 `rules.py`。"""

import uuid

from sqlmodel import and_, col, exists, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Incident, IncidentClause, IncidentTodo


async def policy_todos(
    session: AsyncSession, policy_id: uuid.UUID
) -> list[IncidentTodo]:
    """一張保單的所有待辦：各事件的待辦，加上整個行程共用的（共同文件）。

    不符保障的事件裡，有範本的（文件類）待辦不算：行程頁把它們反灰、不計入完成度與送件檢查
    （ADR-0029；同 `app.domain.incident.trip._greyed`）。
    """
    incident_ids = select(Incident.id).where(Incident.policy_id == policy_id)
    rows = await session.exec(
        select(IncidentTodo).where(
            or_(
                col(IncidentTodo.incident_id).in_(incident_ids),
                IncidentTodo.policy_id == policy_id,
            ),
            ~and_(
                col(IncidentTodo.todo_template_id).is_not(None),
                col(IncidentTodo.incident_id).in_(_uncovered_incident_ids(policy_id)),
            ),
        )
    )
    return list(rows.all())


def _uncovered_incident_ids(policy_id: uuid.UUID):
    """不符保障的事件：有條款結論，但沒有任何一項是 applicable（同 `IncidentContext.is_covered`）。"""
    clause = select(IncidentClause.id).where(IncidentClause.incident_id == Incident.id)
    return select(Incident.id).where(
        Incident.policy_id == policy_id,
        exists(clause),
        ~exists(clause.where(IncidentClause.conclusion == "applicable")),
    )
