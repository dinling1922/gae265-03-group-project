"""使用者刪除已上傳的文件（契約 v2.17）：雲端的檔案與紀錄都拿掉，靠它完成的待辦退回待上傳。"""

import uuid

import pytest
from app.contracts import DocumentView
from app.contracts.fakes import FakeDocumentRecognizer
from app.models import IncidentDocument, IncidentTodo, Policy
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from .test_trip_api import new_incident, trip
from .test_trip_documents_api import shared_todo, upload

pytestmark = pytest.mark.usefixtures("user")


class SpyRecognizer(FakeDocumentRecognizer):
    """記下被請去刪的文件，驗證平台真的請 6 號刪雲端的檔案。"""

    def __init__(self, ok: bool = True) -> None:
        self.discarded: list[DocumentView] = []
        self.ok = ok

    async def discard_trip(self, trip, document, user, google) -> bool:  # type: ignore[no-untyped-def]
        self.discarded.append(document)
        return self.ok


async def uploaded_shared(client: AsyncClient, policy: Policy) -> tuple[dict, str]:
    await new_incident(client, policy)
    todo = shared_todo(await trip(client, policy))
    document = (await upload(client, policy, "診斷.pdf", todo["id"])).json()
    assert shared_todo(await trip(client, policy))["status"] == "done"
    return todo, document["id"]


async def test_刪除共同文件_檔案與紀錄拿掉_待辦退回待上傳(
    client: AsyncClient,
    policy: Policy,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = SpyRecognizer()
    monkeypatch.setattr(
        "app.api.routes.trip_documents.get_document_recognizer", lambda: spy
    )
    todo, document_id = await uploaded_shared(client, policy)

    response = await client.delete(f"/policies/{policy.id}/documents/{document_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["shared_documents"] == []
    after = shared_todo(body)
    assert after["id"] == todo["id"] and after["status"] == "pending"
    assert after["is_done"] is False and after["without_document"] is False
    assert (await session.exec(select(IncidentDocument))).all() == []
    assert [str(d.id) for d in spy.discarded] == [document_id]


async def test_雲端刪不掉_紀錄還是拿掉(
    client: AsyncClient,
    policy: Policy,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = SpyRecognizer(ok=False)
    monkeypatch.setattr(
        "app.api.routes.trip_documents.get_document_recognizer", lambda: spy
    )
    _, document_id = await uploaded_shared(client, policy)

    response = await client.delete(f"/policies/{policy.id}/documents/{document_id}")

    assert response.status_code == 200, response.text
    assert (await session.exec(select(IncidentDocument))).all() == []


async def test_待辦還有別份附件時不退回(
    client: AsyncClient, policy: Policy, session: AsyncSession
) -> None:
    todo, first_id = await uploaded_shared(client, policy)
    second = IncidentDocument(
        policy_id=policy.id,
        incident_todo_id=uuid.UUID(todo["id"]),
        file_name="補充.pdf",
        storage_path="drive-file-2",
        content_type="application/pdf",
        size_bytes=1,
        recognition_status="recognized",
    )
    session.add(second)
    await session.commit()

    response = await client.delete(f"/policies/{policy.id}/documents/{first_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["id"] for d in body["shared_documents"]] == [str(second.id)]
    assert shared_todo(body)["status"] == "done"


async def test_刪掉沒通過的文件_待辦不動(
    client: AsyncClient, policy: Policy, session: AsyncSession
) -> None:
    todo, _ = await uploaded_shared(client, policy)
    bad = IncidentDocument(
        policy_id=policy.id,
        incident_todo_id=uuid.UUID(todo["id"]),
        file_name="糊掉.pdf",
        storage_path="",
        content_type="application/pdf",
        size_bytes=1,
        recognition_status="incomplete",
    )
    session.add(bad)
    await session.commit()

    response = await client.delete(f"/policies/{policy.id}/documents/{bad.id}")

    assert response.status_code == 200, response.text
    assert shared_todo(response.json())["status"] == "done"
    still = (await session.exec(select(IncidentTodo))).all()
    assert any(t.status == "done" for t in still)


async def test_找不到或不是這個行程的文件_404(
    client: AsyncClient, policy: Policy
) -> None:
    await new_incident(client, policy)

    response = await client.delete(f"/policies/{policy.id}/documents/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_事件版_刪除文件並退回待辦(
    client: AsyncClient, policy: Policy, session: AsyncSession
) -> None:
    incident_id = await new_incident(client, policy)
    state = (await client.get(f"/incidents/{incident_id}")).json()
    todo = next(t for t in state["todos"] if t["requires_upload"])
    created = await client.post(
        f"/incidents/{incident_id}/documents",
        files={"file": ("證明.pdf", b"%PDF-1.4", "application/pdf")},
        data={"todo_id": todo["id"]},
    )
    assert created.status_code == 201, created.text

    response = await client.delete(
        f"/incidents/{incident_id}/documents/{created.json()['id']}"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["documents"] == []
    after = next(t for t in body["todos"] if t["id"] == todo["id"])
    assert after["status"] == "pending"
    assert (await session.exec(select(IncidentDocument))).all() == []
