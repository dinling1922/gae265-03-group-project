"""就醫：安達傷害醫療 CHUBB02 與海外突發疾病 CHUBB04 依險種各自下結論（#546）。

平台對沒有章名的安達險種不展開保障（事故類型的保障是空的），所以就醫的保障由 5 號從保單挑出
已核對的險種（CHUBB02、CHUBB04）。生病或受傷已確定時只列符合的那個險種（生病 CHUBB04、受傷 CHUBB02），
保單沒有符合的險種才照舊列出不符的並寫明原因；還不知道生病或受傷時兩個都列。
其他保險公司的就醫仍然沒核對，一律資訊不足。
"""

import uuid
from datetime import date

from app.contracts import AgentServices, IncidentContext, IncidentReply
from app.contracts.common import CoverageRef, RuleRef, TodoView
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
ALL_CHUBB = tuple(f"CHUBB0{i}" for i in range(1, 9))  # 安達進階型
ILLNESS_ONLY = ("CHUBB01", "CHUBB03", "CHUBB04")  # 沒有傷害醫療 CHUBB02 的保單


def chubb_ctx(
    *,
    codes: tuple[str, ...] = ALL_CHUBB,
    facts: dict[str, str] | None = None,
    description: str = "我骨折了",
) -> IncidentContext:
    """安達保單，日本 2026/07/05～07/11；事故類型的保障是空的（同正式環境）。"""
    ctx = medical_ctx(description=description, facts=facts)
    coverages = [CoverageRef(insurance_code=c) for c in codes]
    policy = ctx.policy.model_copy(
        update={
            "plan_id": "chubb_advanced",
            "plan_name": "安達國外旅平險-進階型",
            "start_date": START,
            "end_date": END,
            "insurance_codes": list(codes),
            "coverages": coverages,
        }
    )
    return ctx.model_copy(update={"policy": policy})


def doc_todos(ctx: IncidentContext) -> IncidentContext:
    """就醫事件已有的待辦（平台依 injmed／illmed 的文件建好）：一開始都還沒適用。"""

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
    description: str,
    kind: str,
    turns: list[tuple[str, InputMode]],
    *,
    codes: tuple[str, ...] = ALL_CHUBB,
    services: AgentServices = SERVICES,
) -> tuple[IncidentContext, IncidentReply]:
    """從第一句描述走完追問，回傳最後一輪用的事件與回覆（模擬平台把前面問答記下的狀況帶進來）。"""
    first = await agent(
        extract=extractor(event_type="medical", place="新宿", kind=kind)
    ).start(doc_todos(chubb_ctx(codes=codes, description=description)), services)
    facts = merged({}, first)
    reply, ctx = first, doc_todos(chubb_ctx(codes=codes, description=description))
    for said, mode in turns:
        ctx = doc_todos(chubb_ctx(codes=codes, description=description, facts=facts))
        reply = await agent().reply(ctx, said, mode, services)
        facts = merged(facts, reply)
    return ctx, reply


FULL_TURNS: list[tuple[str, InputMode]] = [
    ("7/8", "text"),
    ("否", "quick_reply"),
    ("是", "quick_reply"),
]


def conclusions(reply: IncidentReply) -> list[tuple[str, str | None]]:
    return [(c.insurance_code, c.conclusion) for c in reply.clauses or []]


def reason_of(reply: IncidentReply) -> str:
    """只列一個險種時，那一列的理由。"""
    [clause] = reply.clauses or []
    return clause.reason or ""


def turned_on(ctx: IncidentContext, reply: IncidentReply) -> set[str]:
    ids = {c.todo_id for c in reply.todo_applicability or [] if c.applicable}
    return {t.title for t in ctx.todos if t.id in ids}


async def test_安達進階型_我骨折了_只列傷害醫療_可能適用_產生就醫待辦() -> None:
    ctx, reply = await walk("我骨折了", "受傷", FULL_TURNS)

    assert reply.finished is True
    assert conclusions(reply) == [("CHUBB02", "applicable")]
    assert "一百八十日" in reason_of(reply)
    # 有可能適用的保障：就醫的文件待辦適用（沒住院，住院證明 D15 不適用）
    assert turned_on(ctx, reply) == {"D13", "D14", "D24", "D25"}


async def test_安達進階型_我感冒去看醫生_只列突發疾病_可能適用_待辦照舊() -> None:
    ctx, reply = await walk("我感冒去看醫生", "生病", FULL_TURNS)

    assert conclusions(reply) == [("CHUBB04", "applicable")]
    assert reason_of(reply).startswith(
        "海外突發疾病門診可申請醫療保險金，依收據在保單額度內理賠"
    )
    assert turned_on(ctx, reply) == {"D13", "D14", "D24", "D25"}


async def test_保單只有突發疾病_受傷_照舊列出不符的並寫明原因_不變成沒有保障() -> None:
    _, reply = await walk("我骨折了", "受傷", FULL_TURNS, codes=ILLNESS_ONLY)

    assert conclusions(reply) == [("CHUBB04", "excluded")]
    assert reason_of(reply).startswith("受傷不屬於海外突發疾病")


async def test_日期不知道但已確定是受傷_只列傷害醫療_理由是請補日期_沒有待辦() -> None:
    ctx, reply = await walk(
        "我骨折了",
        "受傷",
        [("不知道", "quick_reply"), ("否", "quick_reply"), ("是", "quick_reply")],
    )

    assert conclusions(reply) == [("CHUBB02", "insufficient")]
    assert "哪一天發生" in reason_of(reply)
    assert turned_on(ctx, reply) == set()


async def test_日期不知道但已確定是生病_只列突發疾病_理由是請補日期() -> None:
    _, reply = await walk(
        "我感冒去看醫生",
        "生病",
        [("不知道", "quick_reply"), ("否", "quick_reply"), ("是", "quick_reply")],
    )

    assert conclusions(reply) == [("CHUBB04", "insufficient")]
    assert "哪一天發生" in reason_of(reply)


async def test_還不知道是生病還是受傷_兩個險種都列_都是資訊不足() -> None:
    _, reply = await walk("我不舒服去看醫生", "不知道", FULL_TURNS)

    assert conclusions(reply) == [
        ("CHUBB02", "insufficient"),
        ("CHUBB04", "insufficient"),
    ]
    for clause in reply.clauses or []:
        assert clause.reason == "需確認是生病還是受傷"


def recording_search(asked: list[list[str]]):
    """假的條文檢索：每個要求的險種各回一條，記下查了哪些險種。"""

    async def search(_query: str, *, insurance_codes, **_options) -> list[RuleRef]:
        asked.append(list(insurance_codes))
        return [
            RuleRef(
                insurance_code=code,
                article_no=1,
                title="條文",
                content="內容",
                page_start=1,
                page_end=1,
            )
            for code in insurance_codes
        ]

    return search


async def test_受傷_出處只查列出的險種_只有傷害醫療() -> None:
    asked: list[list[str]] = []
    services = AgentServices(search_rules=recording_search(asked))

    _, reply = await walk("我骨折了", "受傷", FULL_TURNS, services=services)

    assert asked[-1] == ["CHUBB02"]
    assert {c.insurance_code for c in reply.citations} == {"CHUBB02"}


async def test_生病_出處只查突發疾病() -> None:
    asked: list[list[str]] = []
    services = AgentServices(search_rules=recording_search(asked))

    _, reply = await walk("我感冒去看醫生", "生病", FULL_TURNS, services=services)

    assert asked[-1] == ["CHUBB04"]
    assert {c.insurance_code for c in reply.citations} == {"CHUBB04"}


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
