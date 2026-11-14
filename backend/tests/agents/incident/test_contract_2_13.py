"""契約 2.13.0（ADR-0029，行程為單位）的相容測試（agent5-trip-page-spec T3）：不用改程式就該通過。

- 共同待辦（`scope="policy"`，D02、D10、D11、D24）整個行程一項，不屬於任何事件：5 號不判斷它們的適用與否。
- `covered=False`（事件沒有任何一項保障可能適用）是平台推導的欄位：5 號照常回答與更正，行為不因它改變。
"""

import uuid

from app.contracts import IncidentContext
from app.contracts.common import ClauseView, TodoView

from tests.agents.incident.test_agent_v1 import (
    ALMOST_DONE,
    QA_REPLY,
    SERVICES,
    agent,
    answers_qa,
    conclusion_of,
    fact,
    make_ctx,
    revising,
)

CONCLUDED = {**ALMOST_DONE, "first_alt": "是"}  # 延誤 6.5 小時：可能適用


def todo(code: str, *, scope: str, applicable: bool = True) -> TodoView:
    return TodoView(
        id=uuid.uuid4(),
        phase="on_site",
        title=code,
        document_code=code,
        scope=scope,  # pyright: ignore[reportArgumentType]
        incident_id=None if scope == "policy" else uuid.uuid4(),
        applicable=applicable,
    )


def uncovered(ctx: IncidentContext) -> IncidentContext:
    """平台推導：有結論、但沒有任何一項可能適用（這裡是不在保障範圍）。"""
    return ctx.model_copy(
        update={
            "covered": False,
            "clauses": [
                ClauseView(
                    insurance_code="FUBON03",
                    section="第三章 班機延誤保險(定額給付-累進式)",
                    conclusion="excluded",
                    reason="延誤未滿四小時，依條款不在保障範圍",
                )
            ],
        }
    )


async def test_更正後結論變了_共同待辦不被回報_只回報事件自己的待辦() -> None:
    own = todo("D01", scope="incident")
    shared_on = todo("D10", scope="policy", applicable=True)
    shared_off = todo("D11", scope="policy", applicable=False)
    ctx = make_ctx(facts=CONCLUDED, type_code="flight_delay").model_copy(
        update={"todos": [own, shared_on, shared_off]}
    )

    # 更正實際起飛時間：延誤變成 2.5 小時，不在保障範圍
    reply = await agent(revise=revising(dep_act="16:30")).reply(
        ctx, "我講錯了，實際是 16:30 起飛", "text", SERVICES
    )

    assert conclusion_of(reply) == "excluded"
    # 只有事件自己的待辦被標成不適用；共同待辦（不論現在適不適用）都沒被動
    assert [(u.todo_id, u.applicable) for u in reply.todo_applicability] == [
        (own.id, False)
    ]


async def test_covered_為_False_的事件_問答照常回答_不改狀況() -> None:
    ctx = uncovered(make_ctx(facts=CONCLUDED, type_code="flight_delay"))

    reply = await agent(answer=answers_qa, revise=revising()).reply(
        ctx, "請問要準備什麼文件？", "text", SERVICES
    )

    assert reply.message == QA_REPLY
    assert reply.facts == [] and reply.clauses is None and reply.todos is None


async def test_covered_為_False_的事件_更正照常更正並重新下結論() -> None:
    ctx = uncovered(
        make_ctx(
            facts={**ALMOST_DONE, "dep_act": "16:30", "first_alt": "是"},
            type_code="flight_delay",
        )
    )

    # 實際起飛時間更正成 22:00：延誤 8 小時，變成可能適用
    reply = await agent(revise=revising(dep_act="22:00")).reply(
        ctx, "我講錯了，實際是 22:00 起飛", "text", SERVICES
    )

    assert fact(reply, "dep_act") == "22:00"
    assert conclusion_of(reply) == "applicable"
