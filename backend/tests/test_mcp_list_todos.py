"""MCP list_todos（#474）：完成度與行程頁一致、依行程→事件分組、輸出允許清單、本人隔離。"""

import json
import uuid
from datetime import UTC, date, datetime

from app.api.deps import current_user
from app.main import app
from app.mcp_server.endpoint import McpEndpoint
from app.mcp_server.tools.list_todos import CUSTOM_TODO_TITLE
from app.models import (
    Conversation,
    Incident,
    IncidentClause,
    IncidentDocument,
    IncidentTodo,
    IncidentType,
    Insurance,
    Policy,
    TodoTemplate,
    User,
)
from httpx import AsyncClient
from mcp import Client
from sqlmodel.ext.asyncio.session import AsyncSession

from tests import test_mcp_endpoint as base

endpoint, clock, rate_limit = base.endpoint, base.clock, base.rate_limit
make_user, make_token, mcp_client = base.make_user, base.make_token, base.mcp_client

TOP_KEYS = {"status", "trips"}
TRIP_KEYS = {
    "trip_id",
    "destination",
    "start_date",
    "end_date",
    "done",
    "total",
    "incidents",
    "shared_documents",
}
INCIDENT_KEYS = {"incident_type", "occurred_date", "todos"}
TODO_KEYS = {"title", "phase", "status"}

SECRETS = [
    "王大明保單 ABC-123456",  # 保單名稱／號碼
    "我在東京被偷了護照",  # 事件標題
    "祕密事件敘述",  # 事件描述
    "我的護照號碼 A123456789",  # 5 號替使用者加的待辦標題
    "護照掃描.jpg",  # 檔名
    "drive-file-id-xyz",  # Drive 檔案 id
    "只給保戶看的注意事項",  # 待辦備註
]


async def build_world(session: AsyncSession, user: User, label: str = "") -> Policy:
    policy = Policy(
        user_id=user.id,
        display_name=SECRETS[0] + label,
        plan_id="fubon_advanced",
        sum_insured=1,
        start_date=date(2026, 12, 1),
        end_date=date(2026, 12, 6),
    )
    insurance = Insurance(
        code=f"T{label or 'X'}{user.id.hex[:6]}", name=f"測試旅平險{label}"
    )
    kind = IncidentType(code=f"medical{label}{user.id.hex[:6]}", name="就醫")
    session.add_all([policy, insurance, kind])
    await session.flush()

    def template(
        title: str, code: str, type_id: uuid.UUID | None = None, shared: bool = False
    ) -> TodoTemplate:
        return TodoTemplate(
            incident_type_id=type_id,
            phase="after_return",
            title=title,
            document_code=code,
            note=SECRETS[6],
            shared=shared,
            is_claim_form=code == "D10",
        )

    t_diag = template("診斷書", "D03", kind.id)
    t_receipt = template("醫療收據", "D04", kind.id)
    t_grey = template("不符保障的文件", "D05", kind.id)
    t_na = template("不適用的文件", "D06", kind.id)
    t_form = template("理賠申請書", "D10", shared=True)
    t_ticket = template("機票與登機證", "D02", shared=True)
    t_pending = template("還沒確認的文件", "D07", kind.id)
    session.add_all([t_diag, t_receipt, t_grey, t_na, t_form, t_ticket, t_pending])
    covered = Incident(
        policy_id=policy.id,
        incident_type_id=kind.id,
        destination="日本",
        title=SECRETS[1],
        description=SECRETS[2],
        occurred_at=datetime(2026, 12, 3, 20, 30, tzinfo=UTC),  # 台北 12/4 04:30
    )
    excluded = Incident(
        policy_id=policy.id,
        incident_type_id=kind.id,
        title="颱風",
        description=SECRETS[2],
    )
    unconfirmed = Incident(
        policy_id=policy.id,
        title="還在問",
        description=SECRETS[2],
        confirmation="clarifying",
    )
    session.add_all([covered, excluded, unconfirmed])
    await session.flush()
    session.add_all(
        Conversation(
            user_id=user.id, policy_id=policy.id, kind="incident_qa", incident_id=i.id
        )
        for i in (covered, excluded, unconfirmed)
    )
    session.add_all(
        [
            IncidentClause(
                incident_id=covered.id,
                insurance_id=insurance.id,
                section="第一章",
                conclusion="applicable",
            ),
            IncidentClause(
                incident_id=excluded.id,
                insurance_id=insurance.id,
                section="第一章",
                conclusion="excluded",
            ),
        ]
    )
    todos = [
        IncidentTodo(
            incident_id=covered.id,
            todo_template_id=t_diag.id,
            document_code="D03",
            phase="on_site",
            title="診斷書",
            note=SECRETS[6],
            sort_order=1,
        ),
        IncidentTodo(
            incident_id=covered.id,
            todo_template_id=t_receipt.id,
            document_code="D04",
            phase="after_return",
            title="醫療收據",
            status="done",
            done_by="user",
            sort_order=2,
        ),
        IncidentTodo(  # 5 號依使用者敘述加的：沒有範本
            incident_id=covered.id,
            phase="after_return",
            title=SECRETS[3],
            sort_order=3,
        ),
        IncidentTodo(
            incident_id=covered.id,
            todo_template_id=t_na.id,
            document_code="D06",
            phase="after_return",
            title="不適用的文件",
            status="not_applicable",
            sort_order=4,
        ),
        IncidentTodo(
            incident_id=covered.id,
            todo_template_id=t_na.id,
            document_code="D08",
            phase="after_return",
            title="依最新狀況不適用的文件",
            applicable=False,
            sort_order=5,
        ),
        IncidentTodo(  # 不符保障事件的文件類待辦：反灰、不計
            incident_id=excluded.id,
            todo_template_id=t_grey.id,
            document_code="D05",
            phase="after_return",
            title="不符保障的文件",
        ),
        IncidentTodo(  # 不符保障事件裡沒有範本的待辦：照樣計入
            incident_id=excluded.id, phase="after_return", title="另外記一筆"
        ),
        IncidentTodo(  # 還沒確認的事件：不算
            incident_id=unconfirmed.id,
            todo_template_id=t_pending.id,
            document_code="D07",
            phase="after_return",
            title="還沒確認的文件",
        ),
        IncidentTodo(
            policy_id=policy.id,
            todo_template_id=t_form.id,
            document_code="D10",
            phase="after_return",
            title="理賠申請書",
        ),
        IncidentTodo(
            policy_id=policy.id,
            todo_template_id=t_ticket.id,
            document_code="D02",
            phase="after_return",
            title="機票與登機證",
            status="done",
            done_by="user",
        ),
    ]
    session.add_all(todos)
    await session.flush()
    session.add(
        IncidentDocument(
            incident_id=covered.id,
            incident_todo_id=todos[0].id,
            file_name=SECRETS[4],
            storage_path=SECRETS[5],
            content_type="image/jpeg",
            size_bytes=1,
        )
    )
    await session.commit()
    return policy


async def call(client: Client, **args: object) -> dict:
    result = await client.call_tool("list_todos", args)
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


def titles(group: dict) -> list[str]:
    return [t["title"] for t in group["todos"]]


async def test_default_lists_only_pending_grouped_by_trip_and_incident(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    policy = await build_world(session, user)
    token = await make_token(session, user)

    async with mcp_client(endpoint, token) as client:
        out = await call(client)

    assert out["status"] == "pending"
    [trip] = out["trips"]
    assert trip["trip_id"] == str(policy.id)
    assert (trip["destination"], trip["start_date"], trip["end_date"]) == (
        "日本",
        "2026-12-01",
        "2026-12-06",
    )
    assert (trip["done"], trip["total"]) == (2, 6)
    # 依事件分組：有發生日的在前（與行程頁相同的順序），未確認的事件不出現
    assert [g["incident_type"] for g in trip["incidents"]] == ["就醫", "就醫"]
    first, second = trip["incidents"]
    assert first["occurred_date"] == "2026-12-04"  # 台北時間
    assert second["occurred_date"] is None
    assert titles(first) == ["診斷書", CUSTOM_TODO_TITLE]
    assert [(t["phase"], t["status"]) for t in first["todos"]] == [
        ("on_site", "pending"),
        ("after_return", "pending"),
    ]
    # 不符保障事件：文件類反灰不列，沒有範本的照樣列
    assert titles(second) == [CUSTOM_TODO_TITLE]
    # 共同文件獨立一組
    assert [t["title"] for t in trip["shared_documents"]] == ["理賠申請書"]


async def test_status_done_lists_only_done(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    await build_world(session, user)
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        out = await call(client, status="done")
        bad = await client.call_tool("list_todos", {"status": "not_applicable"})
    [trip] = out["trips"]
    assert out["status"] == "done"
    assert (trip["done"], trip["total"]) == (2, 6)  # 完成度不受篩選影響
    assert [titles(g) for g in trip["incidents"]] == [["醫療收據"]]
    assert [t["title"] for t in trip["shared_documents"]] == ["機票與登機證"]
    assert bad.is_error


async def test_pending_plus_done_equals_total(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "a@example.com")
    await build_world(session, user)
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        counts = []
        for status in ("pending", "done"):
            [trip] = (await call(client, status=status))["trips"]
            counts.append(
                sum(len(g["todos"]) for g in trip["incidents"])
                + len(trip["shared_documents"])
            )
    assert sum(counts) == 6 == trip["total"]


async def test_completion_matches_trips_page_api(
    session: AsyncSession, client: AsyncClient, endpoint: McpEndpoint
):
    """完成度與行程頁的 GET /trips 同一套規則（未確認事件、不符保障反灰、不適用都一致）。"""
    user = await make_user(session, "a@example.com")
    policy = await build_world(session, user)
    app.dependency_overrides[current_user] = lambda: user
    try:
        web = (await client.get("/trips")).json()
        page = (await client.get(f"/policies/{policy.id}/trip")).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    async with mcp_client(endpoint, await make_token(session, user)) as mcp:
        [trip] = (await call(mcp))["trips"]

    assert [(t["todo_done"], t["todo_total"]) for t in web] == [(2, 6)]
    assert (trip["done"], trip["total"]) == (web[0]["todo_done"], web[0]["todo_total"])
    # 行程頁上不計入的待辦（反灰、不適用）MCP 都沒有列出
    assert any(
        t["title"] == "不符保障的文件" for i in page["incidents"] for t in i["todos"]
    )
    listed = sum(len(g["todos"]) for g in trip["incidents"]) + len(
        trip["shared_documents"]
    )
    assert listed + 2 == trip["total"]  # 未完成 4 + 已完成 2


async def test_only_callers_trips(session: AsyncSession, endpoint: McpEndpoint):
    alice = await make_user(session, "a@example.com")
    bob = await make_user(session, "b@example.com")
    alices = await build_world(session, alice, "甲")
    bobs = await build_world(session, bob, "乙")
    async with mcp_client(endpoint, await make_token(session, alice)) as client:
        assert [t["trip_id"] for t in (await call(client))["trips"]] == [str(alices.id)]
    async with mcp_client(endpoint, await make_token(session, bob)) as client:
        assert [t["trip_id"] for t in (await call(client, status="done"))["trips"]] == [
            str(bobs.id)
        ]


async def test_no_trips_returns_empty(session: AsyncSession, endpoint: McpEndpoint):
    user = await make_user(session, "a@example.com")
    session.add(
        Policy(user_id=user.id, display_name="沒有事件", plan_id="x", sum_insured=1)
    )
    await session.commit()
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        assert await call(client) == {"status": "pending", "trips": []}


async def test_trip_without_matching_todos_still_reports_progress(
    session: AsyncSession, endpoint: McpEndpoint
):
    """全部完成後問預設的未完成：行程仍在，完成數／總數照給，只是沒有待辦項目。"""
    user = await make_user(session, "a@example.com")
    policy = await build_world(session, user)
    from sqlmodel import select

    for t in (await session.exec(select(IncidentTodo))).all():
        t.status = "done"
        session.add(t)
    await session.commit()
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        [trip] = (await call(client))["trips"]
    assert trip["trip_id"] == str(policy.id)
    assert trip["done"] == trip["total"] > 0
    assert trip["incidents"] == [] and trip["shared_documents"] == []


async def test_output_fields_are_an_allowlist(
    session: AsyncSession, endpoint: McpEndpoint
):
    user = await make_user(session, "secret-owner@example.com")
    await build_world(session, user)
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        out = await call(client)
        done = await call(client, status="done")
        raw = str(await client.call_tool("list_todos", {})) + json.dumps(
            done, ensure_ascii=False
        )

    for result in (out, done):
        assert set(result) == TOP_KEYS
        [trip] = result["trips"]
        assert set(trip) == TRIP_KEYS
        for group in trip["incidents"]:
            assert set(group) == INCIDENT_KEYS
            assert all(set(t) == TODO_KEYS for t in group["todos"])
        assert all(set(t) == TODO_KEYS for t in trip["shared_documents"])
    for secret in (*SECRETS, "secret-owner@", "http", "D10", "D03"):
        assert secret not in raw


async def test_tool_is_listed_read_only(session: AsyncSession, endpoint: McpEndpoint):
    user = await make_user(session, "a@example.com")
    async with mcp_client(endpoint, await make_token(session, user)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    tool = tools["list_todos"]
    assert tool.annotations and tool.annotations.read_only_hint is True
    assert "pending" in (tool.description or "") and "done" in (tool.description or "")
