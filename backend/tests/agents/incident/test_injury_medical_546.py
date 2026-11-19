"""就醫：安達傷害醫療 CHUBB02 與海外突發疾病 CHUBB04 依險種各自下結論（#546）。

平台對沒有章名的安達險種不展開保障（事故類型的保障是空的），所以就醫的保障由 5 號從保單挑出
已核對的險種（CHUBB02、CHUBB04）。其他保險公司的就醫仍然沒核對，一律資訊不足。
"""

from datetime import date

from app.contracts import IncidentContext, IncidentReply
from app.contracts.common import CoverageRef, TodoView
from app.contracts.incident_agent import InputMode

from tests.agents.incident.test_agent_v1 import (
    SERVICES,
    agent,
    extractor,
    make_ctx,
    with_coverages,
)
from tests.agents.incident.test_demo_script import medical_ctx, merged

START, END = date(2026, 7, 5), date(2026, 7, 11)
CHUBB_ADVANCED = [CoverageRef(insurance_code=f"CHUBB0{i}") for i in range(1, 9)]


def chubb_advanced_ctx(
    *, facts: dict[str, str] | None = None, description: str = "我骨折了"
) -> IncidentContext:
    """安達進階型（保單帶 CHUBB02、CHUBB04），日本 2026/07/05～07/11；事故類型的保障是空的（同正式環境）。"""
    ctx = medical_ctx(description=description, facts=facts)
    policy = ctx.policy.model_copy(
        update={
            "plan_id": "chubb_advanced",
            "plan_name": "安達國外旅平險-進階型",
            "start_date": START,
            "end_date": END,
            "insurance_codes": [c.insurance_code for c in CHUBB_ADVANCED],
            "coverages": CHUBB_ADVANCED,
        }
    )
    return ctx.model_copy(update={"policy": policy})


def doc_todos(ctx: IncidentContext) -> IncidentContext:
    """就醫事件已有的待辦（平台依 injmed／illmed 的文件建好）：一開始都還沒適用。"""
    import uuid

    def todo(code: str, condition: dict | None = None) -> TodoView:
        return TodoView(
            id=uuid.uuid4(),
            phase="on_site",
            title=code,
            document_code=code,
            applicable=False,
            condition=condition,
        )

    todos = [
        todo("D13"),
        todo("D14"),
        todo("D15", {"field": "hosp", "equals": "是"}),
        todo("D24"),
        todo("D25"),
    ]
    return ctx.model_copy(update={"todos": todos})


async def walk(
    description: str, kind: str, turns: list[tuple[str, InputMode]]
) -> tuple[IncidentContext, IncidentReply]:
    """從第一句描述走完追問，回傳最後一輪用的事件與回覆（模擬平台把前面問答記下的狀況帶進來）。"""
    first = await agent(
        extract=extractor(event_type="medical", place="新宿", kind=kind)
    ).start(doc_todos(chubb_advanced_ctx(description=description)), SERVICES)
    facts = merged({}, first)
    reply, ctx = first, doc_todos(chubb_advanced_ctx(description=description))
    for said, mode in turns:
        ctx = doc_todos(chubb_advanced_ctx(description=description, facts=facts))
        reply = await agent().reply(ctx, said, mode, SERVICES)
        facts = merged(facts, reply)
    return ctx, reply


def by_code(reply: IncidentReply) -> dict:
    return {c.insurance_code: c for c in reply.clauses or []}


async def test_安達進階型_我骨折了_走完追問_傷害醫療可能適用_突發疾病不適用_產生就醫待辦() -> (
    None
):
    ctx, reply = await walk(
        "我骨折了",
        "受傷",
        [("7/8", "text"), ("否", "quick_reply"), ("是", "quick_reply")],
    )

    assert reply.finished is True
    clauses = by_code(reply)
    assert clauses["CHUBB02"].conclusion == "applicable"
    assert "一百八十日" in (clauses["CHUBB02"].reason or "")
    assert clauses["CHUBB04"].conclusion == "excluded"
    assert "傷害醫療" in (clauses["CHUBB04"].reason or "")
    # 有可能適用的保障：就醫的文件待辦適用（沒住院，住院證明 D15 不適用）
    turned_on = {
        t.title
        for t in ctx.todos
        if t.id in {c.todo_id for c in reply.todo_applicability or [] if c.applicable}
    }
    assert turned_on == {"D13", "D14", "D24", "D25"}


async def test_安達進階型_我感冒去看醫生_突發疾病可能適用_傷害醫療不適用_待辦照舊() -> (
    None
):
    ctx, reply = await walk(
        "我感冒去看醫生",
        "生病",
        [("7/8", "text"), ("否", "quick_reply"), ("是", "quick_reply")],
    )

    clauses = by_code(reply)
    assert clauses["CHUBB04"].conclusion == "applicable"
    assert (clauses["CHUBB04"].reason or "").startswith(
        "海外突發疾病門診可申請醫療保險金，依收據在保單額度內理賠"
    )
    assert clauses["CHUBB02"].conclusion == "excluded"
    turned_on = {
        t.title
        for t in ctx.todos
        if t.id in {c.todo_id for c in reply.todo_applicability or [] if c.applicable}
    }
    assert turned_on == {"D13", "D14", "D24", "D25"}


async def test_安達進階型_日期不知道_兩個險種都請旅客補日期_沒有待辦() -> None:
    _, reply = await walk(
        "我骨折了",
        "受傷",
        [("不知道", "quick_reply"), ("否", "quick_reply"), ("是", "quick_reply")],
    )

    clauses = by_code(reply)
    for code in ("CHUBB02", "CHUBB04"):
        assert clauses[code].conclusion == "insufficient"
        assert "哪一天發生" in (clauses[code].reason or "")
    assert not [c for c in reply.todo_applicability or [] if c.applicable]


async def test_富邦受傷就醫_仍是資訊不足_沒核對的險種不套用規則() -> None:
    fubon_medical = CoverageRef(
        insurance_code="FUBON01", section="第三章 傷害醫療費用保險"
    )
    facts = {
        "event_type": "medical",
        "date": "2026/07/08",
        "place": "新宿",
        "kind": "受傷",
        "hosp": "否",
    }
    ctx = with_coverages(
        make_ctx(description="我骨折了", facts=facts, start=START, end=END),
        [fubon_medical],
    )

    reply = await agent().reply(ctx, "是", "quick_reply", SERVICES)

    [clause] = reply.clauses or []
    assert clause.insurance_code == "FUBON01"
    assert clause.conclusion == "insufficient"
    assert "詢問保險公司" in (clause.reason or "")
