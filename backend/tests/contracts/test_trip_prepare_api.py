"""送件前小助理提醒 `POST /policies/{id}/package/prepare`（契約 v2.14）。"""

import uuid

import pytest
from app.contracts import (
    PackageAction,
    PackageActionResult,
    PackagePreparation,
    PackageReminder,
)
from app.models import Policy
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.contracts.test_trip_api import LegacyPackager, new_incident, trip

pytestmark = pytest.mark.usefixtures("user")

EMPTY = {"reminders": [], "summary": None, "actions": []}


async def tick_without_file(client: AsyncClient, policy: Policy) -> dict:
    """勾起一項需要文件、卻沒附任何文件的待辦（信裡會寫「另行提供」）。"""
    body = await trip(client, policy)
    todo = next(t for t in body["incidents"][0]["todos"] if t["requires_upload"])
    response = await client.patch(
        f"/policies/{policy.id}/todos/{todo['id']}", json={"is_done": True}
    )
    assert response.status_code == 200, response.text
    return todo


async def test_假的_7_號提醒勾選了卻沒附文件的待辦(
    client: AsyncClient, policy: Policy
) -> None:
    await new_incident(client, policy)
    todo = await tick_without_file(client, policy)

    response = await client.post(f"/policies/{policy.id}/package/prepare")

    assert response.status_code == 200, response.text
    reminders = response.json()["reminders"]
    assert [r["todo_id"] for r in reminders] == [todo["id"]]
    assert reminders[0]["incident_id"] == todo["incident_id"]
    assert "另行提供" in reminders[0]["text"]


async def test_重複呼叫結果相同_不改動待辦(client: AsyncClient, policy: Policy) -> None:
    await new_incident(client, policy)
    await tick_without_file(client, policy)
    before = await trip(client, policy)

    first = (await client.post(f"/policies/{policy.id}/package/prepare")).json()
    second = (await client.post(f"/policies/{policy.id}/package/prepare")).json()

    assert first == second
    assert await trip(client, policy) == before


async def test_7_號沒有實作_prepare_回空提醒(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.routes.trip.get_claim_packager", lambda: LegacyPackager()
    )
    await new_incident(client, policy)
    await tick_without_file(client, policy)

    response = await client.post(f"/policies/{policy.id}/package/prepare")

    assert response.status_code == 200 and response.json() == EMPTY


async def test_7_號出錯回空提醒_不擋畫面(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        async def prepare_trip(self, trip, google=None, user=None):
            raise RuntimeError("7 號壞了")

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Broken())
    await new_incident(client, policy)

    response = await client.post(f"/policies/{policy.id}/package/prepare")

    assert response.status_code == 200 and response.json() == EMPTY


async def test_提醒指到不存在的事件或待辦就丟掉(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    await new_incident(client, policy)
    todo = next(t for t in (await trip(client, policy))["incidents"][0]["todos"])

    class Sloppy:
        async def prepare_trip(self, trip, google=None, user=None):
            return PackagePreparation(
                reminders=[
                    PackageReminder(text="整個行程的提醒"),
                    PackageReminder(text="真的待辦", todo_id=uuid.UUID(todo["id"])),
                    PackageReminder(text="不存在的事件", incident_id=uuid.uuid4()),
                    PackageReminder(text="不存在的待辦", todo_id=uuid.uuid4()),
                ]
            )

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Sloppy())

    reminders = (await client.post(f"/policies/{policy.id}/package/prepare")).json()[
        "reminders"
    ]

    assert [r["text"] for r in reminders] == ["整個行程的提醒", "真的待辦"]


async def test_7_號只看得到符合保障的事件(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    class Spy:
        async def prepare_trip(self, trip, google=None, user=None):
            seen.append(len(trip.incidents))
            return PackagePreparation()

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Spy())
    await new_incident(client, policy)
    await new_incident(client, policy, "班機延誤了但是不理賠")

    response = await client.post(f"/policies/{policy.id}/package/prepare")

    assert response.status_code == 200
    assert seen == [1]  # 不符保障的事件平台已經濾掉


async def test_全部事件都不符保障時不呼叫_7_號(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Spy:
        async def prepare_trip(self, trip, google=None, user=None):
            raise AssertionError("沒有符合保障的事件，不該呼叫 7 號")

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Spy())
    await new_incident(client, policy, "班機延誤了但是不理賠")

    response = await client.post(f"/policies/{policy.id}/package/prepare")

    assert response.status_code == 200 and response.json() == EMPTY


async def test_行程鎖住後回_409(
    client: AsyncClient, policy: Policy, session: AsyncSession
) -> None:
    await new_incident(client, policy)
    from tests.contracts.test_trip_api import finish_all_todos

    await finish_all_todos(session)
    submitted = await client.post(
        f"/policies/{policy.id}/submission", json={"mode": "self"}
    )
    assert submitted.status_code == 201

    response = await client.post(f"/policies/{policy.id}/package/prepare")

    assert response.status_code == 409


async def test_別人的行程回_404(client: AsyncClient) -> None:
    response = await client.post(f"/policies/{uuid.uuid4()}/package/prepare")

    assert response.status_code == 404


# --- 契約 v2.15：mode、總結、修正 ---


async def test_mode_只傳給簽名有_mode_的實作(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []

    class New:
        async def prepare_trip(self, trip, google=None, user=None, mode=None):
            seen.append(mode)
            return PackagePreparation(summary="總結")

    class Old:  # v2.14 的實作，沒有 mode
        async def prepare_trip(self, trip, google=None, user=None):
            return PackagePreparation(reminders=[PackageReminder(text="舊的提醒")])

    await new_incident(client, policy)
    url = f"/policies/{policy.id}/package/prepare"
    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: New())
    assert (await client.post(url)).json()["summary"] == "總結"
    await client.post(url, params={"mode": "self"})
    await client.post(url, params={"mode": "send"})
    assert seen == [None, "self", "send"]

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Old())
    response = await client.post(url, params={"mode": "send"})
    assert response.status_code == 200
    assert [r["text"] for r in response.json()["reminders"]] == ["舊的提醒"]
    assert (await client.post(url, params={"mode": "bad"})).status_code == 422


async def test_修正指到不存在的文件就丟掉_總結照留(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Sloppy:
        async def prepare_trip(self, trip, google=None, user=None, mode=None):
            return PackagePreparation(
                summary="先還原檔案。",
                actions=[
                    PackageAction(
                        action_id="restore:x",
                        kind="restore_file",
                        label="還原",
                        document_id=uuid.uuid4(),
                    ),
                    PackageAction(
                        action_id="restore:y",
                        kind="restore_file",
                        label="還原",
                        incident_id=uuid.uuid4(),
                    ),
                ],
            )

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Sloppy())
    await new_incident(client, policy)

    body = (await client.post(f"/policies/{policy.id}/package/prepare")).json()

    assert body["actions"] == [] and body["summary"] == "先還原檔案。"


async def test_假的_7_號沒有垃圾桶檔案時沒有修正(
    client: AsyncClient, policy: Policy
) -> None:
    await new_incident(client, policy)
    await tick_without_file(client, policy)

    body = (await client.post(f"/policies/{policy.id}/package/prepare")).json()

    assert body["actions"] == [] and body["summary"] is not None


async def test_執行修正_呼叫_apply_action(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class Fixer:
        async def apply_action(self, trip, action_id, google, user):
            calls.append(action_id)
            return PackageActionResult(ok=True, message="已還原")

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Fixer())
    await new_incident(client, policy)

    response = await client.post(f"/policies/{policy.id}/package/actions/restore:abc")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "已還原"}
    assert calls == ["restore:abc"]


async def test_apply_action_出錯回_ok_false_不丟例外(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        async def apply_action(self, trip, action_id, google, user):
            raise RuntimeError("壞了")

    monkeypatch.setattr("app.api.routes.trip.get_claim_packager", lambda: Broken())
    await new_incident(client, policy)

    response = await client.post(f"/policies/{policy.id}/package/actions/restore:abc")

    assert response.status_code == 200 and response.json()["ok"] is False


async def test_7_號沒有實作_apply_action_回_409(
    client: AsyncClient, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.routes.trip.get_claim_packager", lambda: LegacyPackager()
    )
    await new_incident(client, policy)

    response = await client.post(f"/policies/{policy.id}/package/actions/restore:abc")

    assert response.status_code == 409


async def test_修正_行程鎖住回_409_別人的行程回_404(
    client: AsyncClient, policy: Policy, session: AsyncSession
) -> None:
    from tests.contracts.test_trip_api import finish_all_todos

    await new_incident(client, policy)
    await finish_all_todos(session)
    await client.post(f"/policies/{policy.id}/submission", json={"mode": "self"})

    locked = await client.post(f"/policies/{policy.id}/package/actions/restore:abc")
    other = await client.post(f"/policies/{uuid.uuid4()}/package/actions/restore:abc")

    assert locked.status_code == 409 and other.status_code == 404
