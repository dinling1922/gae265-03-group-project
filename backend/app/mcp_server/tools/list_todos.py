"""list_todos：呼叫者本人的理賠待辦，依行程（保單）→ 事件分組（唯讀）。

規則與行程頁同一套：行程與待辦由 `trip.build_trip_context` 組出（未確認的事件不算），
完成數／總數與列出的待辦都來自 `trip.counted_todos`（不符保障事件裡反灰的文件類待辦、不適用的待辦不計），
所以 AI 說的完成度與行程頁、`GET /trips` 一致（ADR-0029、ADR-0032）。
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from sqlmodel import col, select

from app.contracts import TodoView, TripContext
from app.domain.incident import trip as trip_domain
from app.domain.reminder.rounds import TAIPEI
from app.mcp_server.context import tool_env
from app.mcp_server.registry import mcp_tool
from app.models import Incident, Policy, TodoTemplate

# 沒有範本的待辦是 5 號依對話替使用者加的，標題可能含使用者敘述，不放
CUSTOM_TODO_TITLE = "其他待辦（內容請看網頁）"

TodoStatus = Literal["pending", "done"]


class TodoOut(BaseModel):
    title: str = Field(description="待辦標題（範本的固定文字）")
    phase: Literal["on_site", "after_return"] = Field(
        description="階段：on_site 事故當下、after_return 回國後整理"
    )
    status: TodoStatus = Field(description="pending 未完成、done 已完成")


class IncidentTodosOut(BaseModel):
    incident_type: str | None = Field(description="事故類型名稱；還沒判斷出來是 null")
    occurred_date: date | None = Field(
        description="事故發生日（台北時間）；不知道是 null"
    )
    todos: list[TodoOut]


class TripTodosOut(BaseModel):
    """允許清單：不含保單號碼、被保險人姓名、方案、檔名、Drive 連結、事件的標題與敘述。"""

    trip_id: uuid.UUID = Field(
        description="行程代碼（行程＝一張保單，這個代碼不是保單號碼）"
    )
    destination: str | None = Field(description="行程的目的地")
    start_date: date | None = Field(description="保險期間起")
    end_date: date | None = Field(description="保險期間迄")
    done: int = Field(
        description="已完成的待辦數（與行程頁一致，不受 status 篩選影響）"
    )
    total: int = Field(description="全部待辦數（與行程頁一致，不受 status 篩選影響）")
    incidents: list[IncidentTodosOut] = Field(
        description="依事件分組；沒有符合篩選的待辦的事件不列"
    )
    shared_documents: list[TodoOut] = Field(
        description="共同文件（理賠申請書等整個行程只收一份的文件），不屬於任何事件"
    )


class ListTodosOut(BaseModel):
    status: TodoStatus = Field(description="這次篩選的狀態")
    trips: list[TripTodosOut]


def _wanted(todo: TodoView, status: TodoStatus) -> bool:
    return todo.is_done == (status == "done")


async def _titles(session, trips: list[TripContext]) -> dict[uuid.UUID, str]:
    """範本的標題：待辦自己的 title 是建立時複製的，這裡改取範本的固定文字。"""
    ids = {t.template_id for trip in trips for t in trip.todos if t.template_id}
    if not ids:
        return {}
    rows = await session.exec(
        select(TodoTemplate.id, TodoTemplate.title).where(col(TodoTemplate.id).in_(ids))
    )
    return dict(rows.all())


@mcp_tool
async def list_todos(
    status: Annotated[
        TodoStatus,
        Field(description="pending 只列未完成（預設）；done 只列已完成"),
    ] = "pending",
) -> ListTodosOut:
    """列出使用者自己的理賠待辦，依行程（一張保單）再依事件分組；共同文件獨立一組。

    每個行程附完成數與總數（done／total，與網頁行程頁一致，不受 status 篩選影響）。
    每項待辦有標題、階段（on_site 事故當下／after_return 回國後整理）與狀態，事件附事故類型與發生日。
    預設只列未完成（status=pending），要看已完成的用 status=done。
    還沒確認的事件不算；不符保障事件的文件類待辦與不適用的待辦不列也不計。
    行程最新建立的在前。只讀取，不會勾選或修改任何待辦。
    """
    env = tool_env()
    async with env.session_maker() as session:
        policies = (
            await session.exec(
                select(Policy)
                .where(
                    Policy.user_id == env.identity.user.id,
                    col(Policy.id).in_(
                        select(Incident.policy_id).where(
                            Incident.confirmation == "confirmed"
                        )
                    ),
                )
                .order_by(col(Policy.created_at).desc(), col(Policy.id).desc())
            )
        ).all()
        trips = [await trip_domain.build_trip_context(session, p) for p in policies]
        titles = await _titles(session, trips)

        def todo_out(t: TodoView) -> TodoOut:
            title = titles.get(t.template_id) if t.template_id else None
            return TodoOut(
                title=title or CUSTOM_TODO_TITLE,
                phase=t.phase,
                status="done" if t.is_done else "pending",
            )

        out: list[TripTodosOut] = []
        for policy, trip in zip(policies, trips, strict=True):
            counted = [t for t in trip_domain.counted_todos(trip) if _wanted(t, status)]
            done, total = trip_domain.progress(trip)
            by_incident: dict[uuid.UUID | None, list[TodoView]] = {}
            for t in counted:
                by_incident.setdefault(t.incident_id, []).append(t)
            out.append(
                TripTodosOut(
                    trip_id=policy.id,
                    destination=trip_domain.destination_of(trip),
                    start_date=policy.start_date,
                    end_date=policy.end_date,
                    done=done,
                    total=total,
                    incidents=[
                        IncidentTodosOut(
                            incident_type=i.incident_type_name,
                            occurred_date=i.occurred_at.astimezone(TAIPEI).date()
                            if i.occurred_at
                            else None,
                            todos=[todo_out(t) for t in by_incident[i.incident_id]],
                        )
                        for i in trip.incidents
                        if i.incident_id in by_incident
                    ],
                    shared_documents=[todo_out(t) for t in by_incident.get(None, [])],
                )
            )
        return ListTodosOut(status=status, trips=out)
