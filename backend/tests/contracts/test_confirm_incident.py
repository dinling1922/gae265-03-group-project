"""2-2 現況確認（#345）：事件照常套用但標成未確認；確認前不進行程、共同待辦不建立，確認後和一般事件一樣。"""

import uuid

import pytest
from app.contracts import IncidentReply
from app.contracts.fakes import FakeIncidentAgent
from app.domain.incident.service import next_confirmation
from app.models import Incident, IncidentTodo, Policy
from httpx import AsyncClient
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.usefixtures("user")


async def start_pending(client: AsyncClient, policy: Policy) -> str:
    created = await client.post(
        f"/policies/{policy.id}/incidents",
        json={"description": "班機延誤了", "require_confirmation": True},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["confirmation"] == "clarifying"
    assert body["show_confirm"] is False
    return body["incident"]["incident_id"]


async def talk_until_finished(client: AsyncClient, incident_id: str) -> dict:
    for i in range(6):
        response = await client.post(
            f"/incidents/{incident_id}/messages",
            json={"content": "班機延誤了六小時" if i == 0 else "不知道"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if body["reply"]["finished"]:
            return body
    raise AssertionError("5 號一直沒結束")


def test_狀態轉換表() -> None:
    f = next_confirmation
    # 已確認：只有「問完 + 結論或適用待辦有變」才跳（補完資訊後再確認，#445），狀態維持已確認
    assert f("confirmed", finished=True, changed=True) == ("confirmed", False)
    assert f("confirmed", finished=True, changed=True, outcome_changed=False) == (
        "confirmed",
        False,
    )
    assert f("confirmed", finished=True, changed=True, outcome_changed=True) == (
        "confirmed",
        True,
    )
    assert f("confirmed", finished=False, changed=True, outcome_changed=True) == (
        "confirmed",
        False,
    )
    assert f("clarifying", finished=True, changed=False) == ("awaiting", True)
    assert f("clarifying", finished=False, changed=True) == ("clarifying", False)
    # 等確認時：沒變不論 finished 都維持、不跳；有變且 finished 再跳；有變但沒問完退回
    assert f("awaiting", finished=True, changed=False) == ("awaiting", False)
    assert f("awaiting", finished=False, changed=False) == ("awaiting", False)
    assert f("awaiting", finished=True, changed=True) == ("awaiting", True)
    assert f("awaiting", finished=False, changed=True) == ("clarifying", False)


async def test_一般事件和既有行為一樣_直接是已確認(
    client: AsyncClient, policy: Policy
) -> None:
    created = await client.post(
        f"/policies/{policy.id}/incidents", json={"description": "班機延誤了"}
    )
    assert created.json()["confirmation"] == "confirmed"
    assert created.json()["show_confirm"] is False


async def test_問完才跳彈窗_確認前不進行程_確認後建立共同待辦(
    client: AsyncClient, session: AsyncSession, policy: Policy
) -> None:
    incident_id = await start_pending(client, policy)
    body = await talk_until_finished(client, incident_id)
    assert body["confirmation"] == "awaiting"
    assert body["show_confirm"] is True
    assert body["incident"]["todos"]  # 照常套用，5 號的 ctx 有待辦

    # 確認前：行程裡看不到這個事件，共同待辦還沒建立，行程列表顯示它是等確認的
    assert (await client.get(f"/policies/{policy.id}/trip")).status_code == 404
    assert not (
        await session.exec(
            select(IncidentTodo).where(IncidentTodo.policy_id == policy.id)
        )
    ).all()  # 共同待辦還沒建立
    row = next(t for t in (await client.get("/trips")).json())
    assert row["state"] == "describing"
    assert row["pending_incident_id"] == incident_id
    assert row["incident_count"] == 0

    confirmed = await client.post(f"/incidents/{incident_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {
        "policy_id": str(policy.id),
        "confirmation": "confirmed",
    }
    trip = (await client.get(f"/policies/{policy.id}/trip")).json()
    assert [i["id"] for i in trip["incidents"]] == [incident_id]
    assert trip["shared_todos"]
    row = next(t for t in (await client.get("/trips")).json())
    assert row["state"] != "describing"
    assert row["pending_incident_id"] is None
    assert (await client.post(f"/incidents/{incident_id}/confirm")).status_code == 409


async def test_確認後的待辦和不經確認的事件完全一樣(
    client: AsyncClient, session: AsyncSession, policy: Policy
) -> None:
    """正式站劇本固定是 7 項：確認流程不能多或少任何一項（#345 5 號反饋 6）。"""
    other = Policy(
        user_id=policy.user_id,
        display_name="另一張",
        plan_id="fubon_advanced",
        sum_insured=6_000_000,
    )
    session.add(other)
    await session.commit()

    plain = (
        await client.post(
            f"/policies/{other.id}/incidents", json={"description": "班機延誤了"}
        )
    ).json()["incident"]["incident_id"]
    await talk_until_finished(client, plain)

    pending = await start_pending(client, policy)
    await talk_until_finished(client, pending)
    await client.post(f"/incidents/{pending}/confirm")

    async def titles(policy_id: uuid.UUID) -> list[tuple[str, str]]:
        rows = (
            await session.exec(
                select(IncidentTodo)
                .where(
                    col(IncidentTodo.incident_id).in_(
                        select(Incident.id).where(Incident.policy_id == policy_id)
                    )
                    | (IncidentTodo.policy_id == policy_id)
                )
                .order_by(col(IncidentTodo.phase), col(IncidentTodo.title))
            )
        ).all()
        return [(t.phase, t.title) for t in rows]

    assert await titles(policy.id) == await titles(other.id)
    assert len(await titles(policy.id)) > 0


async def test_等確認時給_5_號的_ctx_和已確認的事件一樣(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict] = []

    class Spy(FakeIncidentAgent):
        async def reply(self, ctx, user_message, input_mode, services):  # type: ignore[override]
            seen.append(
                ctx.model_dump(mode="json", exclude={"incident_id", "messages"})
                # 共同待辦要等確認才建立，ctx 裡比對的是這個事故自己的待辦
                | {"n_todos": sum(1 for t in ctx.todos if t.incident_id is not None)}
            )
            return await super().reply(ctx, user_message, input_mode, services)

    monkeypatch.setattr("app.api.routes.incidents.get_incident_agent", Spy)
    incident_id = await start_pending(client, policy)
    await talk_until_finished(client, incident_id)  # 到 awaiting
    await client.post(
        f"/incidents/{incident_id}/messages", json={"content": "補充一下"}
    )
    awaiting_ctx = seen[-1]
    await client.post(f"/incidents/{incident_id}/confirm")
    await client.post(
        f"/incidents/{incident_id}/messages", json={"content": "再補充一下"}
    )
    confirmed_ctx = seen[-1]
    assert awaiting_ctx["n_todos"] == confirmed_ctx["n_todos"] > 0
    assert awaiting_ctx["facts"] == confirmed_ctx["facts"]
    assert awaiting_ctx["clauses"] == confirmed_ctx["clauses"]
    assert awaiting_ctx["incident_type_code"] == confirmed_ctx["incident_type_code"]


async def test_等確認時只是問問題_不跳彈窗_維持等確認(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident_id = await start_pending(client, policy)
    await talk_until_finished(client, incident_id)

    class JustAnswer(FakeIncidentAgent):
        async def reply(self, ctx, user_message, input_mode, services):  # type: ignore[override]
            return IncidentReply(message="這是條款說明", finished=True)

    monkeypatch.setattr("app.api.routes.incidents.get_incident_agent", JustAnswer)
    body = (
        await client.post(
            f"/incidents/{incident_id}/messages", json={"content": "這樣會賠嗎？"}
        )
    ).json()
    assert body["changed"] is False
    assert body["show_confirm"] is False
    assert body["confirmation"] == "awaiting"


async def test_還在問的事件不能確認(client: AsyncClient, policy: Policy) -> None:
    incident_id = await start_pending(client, policy)
    response = await client.post(f"/incidents/{incident_id}/confirm")
    assert response.status_code == 409


async def test_別人的事件確認回_404(client: AsyncClient) -> None:
    response = await client.post(f"/incidents/{uuid.uuid4()}/confirm")
    assert response.status_code == 404


class _InsufficientUntilActual(FakeIncidentAgent):
    """問完先是資訊不足（沒有待辦）；使用者補了「實際」的資訊才變成可能適用（Demo 的班機延誤）。"""

    async def reply(self, ctx, user_message, input_mode, services):  # type: ignore[override]
        reply = await super().reply(ctx, user_message, input_mode, services)
        if not reply.finished:
            return reply
        actual = any("實際" in m.content for m in ctx.messages if m.role == "user")
        if actual:
            return reply
        return reply.model_copy(
            update={
                "clauses": [
                    c.model_copy(update={"conclusion": "insufficient"})
                    for c in reply.clauses or []
                ],
                "todos": [],
            }
        )


async def _confirmed_incident(client: AsyncClient, policy: Policy) -> str:
    """一般建立（不經確認彈窗，像行程已有事件時的新狀況）並問完；問完的每一輪都不該跳彈窗。"""
    created = await client.post(
        f"/policies/{policy.id}/incidents", json={"description": "班機延誤了"}
    )
    assert created.status_code == 201, created.text
    incident_id = created.json()["incident"]["incident_id"]
    for i in range(6):
        body = (
            await client.post(
                f"/incidents/{incident_id}/messages",
                json={"content": "班機延誤了六小時" if i == 0 else "不知道"},
            )
        ).json()
        assert body["confirmation"] == "confirmed"
        assert body["show_confirm"] is False  # 第一次問完不是「補完資訊後」，不跳
        if body["reply"]["finished"]:
            return incident_id
    raise AssertionError("5 號一直沒結束")


async def test_已確認的事件_補完資訊讓結論從資訊不足變成可申請_跳彈窗_狀態維持已確認(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.routes.incidents.get_incident_agent", _InsufficientUntilActual
    )
    incident_id = await _confirmed_incident(client, policy)
    before = (await client.get(f"/incidents/{incident_id}")).json()
    assert {c["conclusion"] for c in before["clauses"]} == {"insufficient"}

    body = (
        await client.post(
            f"/incidents/{incident_id}/messages", json={"content": "實際 16:45 起飛"}
        )
    ).json()
    assert body["changed"] is True
    assert body["show_confirm"] is True
    assert body["confirmation"] == "confirmed"
    after = (await client.get(f"/incidents/{incident_id}")).json()
    assert {c["conclusion"] for c in after["clauses"]} == {"applicable"}


async def test_已確認的事件_只問條款_結論沒變_不跳彈窗(
    client: AsyncClient, policy: Policy
) -> None:
    incident_id = await _confirmed_incident(client, policy)
    body = (
        await client.post(
            f"/incidents/{incident_id}/messages", json={"content": "這樣會賠嗎？"}
        )
    ).json()
    assert body["changed"] is False
    assert body["show_confirm"] is False
    assert body["confirmation"] == "confirmed"


async def test_已確認的事件_只改了事實_結論與待辦沒變_不跳彈窗(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.contracts import FactUpdate

    incident_id = await _confirmed_incident(client, policy)

    class OnlyFact(FakeIncidentAgent):
        async def reply(self, ctx, user_message, input_mode, services):  # type: ignore[override]
            reply = await super().reply(ctx, user_message, input_mode, services)
            return reply.model_copy(
                update={
                    "facts": [
                        FactUpdate(fact_key="place", label="發生地點", value="成田機場")
                    ]
                }
            )

    monkeypatch.setattr("app.api.routes.incidents.get_incident_agent", OnlyFact)
    body = (
        await client.post(
            f"/incidents/{incident_id}/messages", json={"content": "在成田機場"}
        )
    ).json()
    assert body["changed"] is True  # 事實有變
    assert body["show_confirm"] is False  # 但結論與待辦都沒變，不跳
