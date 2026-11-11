"""提醒信從資料庫讀的資料（ADR-0028）；規則本身在 `rules.py`。"""

import uuid

from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Incident, IncidentTodo


async def policy_todos(
    session: AsyncSession, policy_id: uuid.UUID
) -> list[IncidentTodo]:
    """一張保單的所有待辦：各事件的待辦，加上整個行程共用的（理賠申請書）。"""
    incident_ids = select(Incident.id).where(Incident.policy_id == policy_id)
    rows = await session.exec(
        select(IncidentTodo).where(
            or_(
                col(IncidentTodo.incident_id).in_(incident_ids),
                IncidentTodo.policy_id == policy_id,
            )
        )
    )
    return list(rows.all())
