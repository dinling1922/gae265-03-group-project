"""7 號的行程版 build_trip、send_trip（契約 v2.13、ADR-0029）：整個行程一封信。

自己組行程資料，不連資料庫；寄送用假的 Google 轉接器，不連 Google。
"""

import re

from app.agents.package import GmailClaimPackager
from app.contracts import (
    ClaimPackage,
    DocumentView,
    FactView,
    IncidentContext,
    TodoView,
    TripClaimPackager,
    TripContext,
)

from .test_build import _document, _incident, _policy, _todo
from .test_send_gmail import BOTH, DELAY, FORM, USER, FakeGoogleAccess, FakeGoogleApi


def _owned(todo: TodoView, incident: IncidentContext | None) -> TodoView:
    """待辦屬於哪個事件；共同文件（D02、D10 等）是 None。"""
    return todo.model_copy(
        update={"incident_id": incident.incident_id if incident else None}
    )


def _delay(
    *,
    todos: list[TodoView] | None = None,
    documents: list[DocumentView] | None = None,
    facts: list[FactView] | None = None,
    description: str = "班機延誤了六小時",
) -> IncidentContext:
    return _incident(
        documents,
        todos,
        _policy(),
        description=description,
        incident_type_name="班機延誤",
        facts=facts,
    )


def _trip(
    *incidents: IncidentContext,
    shared_todos: list[TodoView] | None = None,
    shared_documents: list[DocumentView] | None = None,
) -> TripContext:
    return TripContext(
        policy=_policy(),
        incidents=list(incidents),
        shared_todos=shared_todos or [],
        shared_documents=shared_documents or [],
    )


def test_正式版有行程版介面_平台才會讓使用者寄出與建草稿() -> None:
    # 平台用 isinstance(…, TripClaimPackager) 決定 can_send
    assert isinstance(GmailClaimPackager(), TripClaimPackager)


async def test_一封信涵蓋全部事件_問候與保單資訊只寫一次() -> None:
    luggage = _incident(incident_type_name="行李延誤", description="行李晚到一天")
    package = await GmailClaimPackager().build_trip(_trip(_delay(), luggage))

    assert package.body.count("您好：") == 1
    assert package.body.count("保單資訊") == 1
    assert package.body.count("實際能否理賠請以保險公司審核為準") == 1
    summary = package.body.split("事故摘要\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert len(summary) == 2
    assert summary[0].startswith("・班機延誤 · ") and summary[0].endswith(
        "：班機延誤了六小時"
    )
    assert summary[1].startswith("・行李延誤 · ") and summary[1].endswith(
        "：行李晚到一天"
    )


async def test_主旨和事件版相同() -> None:
    incident = _delay()
    trip_package = await GmailClaimPackager().build_trip(_trip(incident))
    incident_package = await GmailClaimPackager().build(incident)

    assert trip_package.subject == incident_package.subject


async def test_事故的空格用事故名稱區分_同類型第二次加半形序號() -> None:
    package = await GmailClaimPackager().build_trip(
        _trip(_delay(), _delay(description=""))
    )

    assert "（請填寫班機延誤日期）" in package.body
    assert "（請填寫班機延誤(2)日期）" in package.body
    assert "（請填寫班機延誤(2)經過）" in package.body
    # 摘要的開頭也寫同一個名稱，看得出空格是哪個事故的
    assert "\n・班機延誤 · " in package.body
    assert "\n・班機延誤(2) · " in package.body
    # 整封信只有一份的空格不加事故名稱
    assert "（請填寫被保險人姓名）" in package.body
    assert "（請填寫聯絡電話）" in package.body


async def test_能換算的日期照寫_不留空格() -> None:
    date = FactView(fact_key="date", label="發生日期", value="2026/8/10")

    package = await GmailClaimPackager().build_trip(_trip(_delay(facts=[date])))

    assert "・班機延誤 · 2026/08/10 · " in package.body
    assert "班機延誤日期" not in package.body


async def test_附件照事件順序_共同文件排最後且只附一次() -> None:
    first, second = _delay(), _delay()
    proof1 = _owned(_todo("done", title="延誤證明"), first)
    proof2 = _owned(_todo("done", title="延誤證明"), second)
    claim_form = _owned(_todo("done", title="理賠申請書", sort_order=9), None)
    boarding = _owned(_todo("done", title="登機證", sort_order=1), None)
    first = first.model_copy(
        update={
            "todos": [proof1],
            "documents": [_document("recognized", todo=proof1, file_name="a.pdf")],
        }
    )
    second = second.model_copy(
        update={
            "todos": [proof2],
            "documents": [_document("recognized", todo=proof2, file_name="b.pdf")],
        }
    )
    trip = _trip(
        first,
        second,
        shared_todos=[claim_form, boarding],
        shared_documents=[
            _document("recognized", todo=claim_form, file_name="理賠申請書_1.pdf"),
            _document("recognized", todo=boarding, file_name="登機證_1.pdf"),
        ],
    )

    package = await GmailClaimPackager().build_trip(trip)

    assert [a.file_name for a in package.attachments] == [
        "a.pdf",
        "b.pdf",
        "登機證_1.pdf",
        "理賠申請書_1.pdf",
    ]
    assert "相關文件如附件" in package.body
    assert package.missing_todo_ids == []


async def test_未隨信附上的文件_事件的待辦加事故名稱_共同文件只寫名稱() -> None:
    first, second = _delay(), _delay()
    skipped1 = _owned(_todo("done", title="延誤證明", without_document=True), first)
    skipped2 = _owned(_todo("done", title="延誤證明", without_document=True), second)
    shared = _owned(_todo("done", title="理賠申請書", without_document=True), None)
    trip = _trip(
        first.model_copy(update={"todos": [skipped1]}),
        second.model_copy(update={"todos": [skipped2]}),
        shared_todos=[shared],
    )

    package = await GmailClaimPackager().build_trip(trip)

    assert (
        "未隨信附上的文件\n"
        "・班機延誤：延誤證明（另行提供）\n"
        "・班機延誤(2)：延誤證明（另行提供）\n"
        "・理賠申請書（另行提供）"
    ) in package.body
    assert "相關文件另行提供" in package.body
    assert "・（無）" in package.body


async def test_還沒完成的待辦含共同文件() -> None:
    incident = _delay()
    pending = _owned(_todo("pending"), incident)
    shared = _owned(_todo("pending", title="理賠申請書"), None)
    trip = _trip(
        incident.model_copy(update={"todos": [pending]}), shared_todos=[shared]
    )

    package = await GmailClaimPackager().build_trip(trip)

    assert set(package.missing_todo_ids) == {pending.id, shared.id}


def _filled(package: ClaimPackage) -> ClaimPackage:
    """使用者在預覽頁把空格都填好（平台會換成填的值）。"""
    fill = re.compile(r"（請填寫[^）]+）")
    return package.model_copy(
        update={
            "subject": fill.sub("測試", package.subject),
            "body": fill.sub("測試", package.body),
        }
    )


async def _ready_trip() -> TripContext:
    incident = _delay()
    proof = _owned(_todo("done", title="延誤證明"), incident)
    form = _owned(_todo("done", title="理賠申請書"), None)
    return _trip(
        incident.model_copy(
            update={
                "todos": [proof],
                "documents": [
                    _document(
                        "recognized",
                        todo=proof,
                        storage_path="drive-1",
                        file_name=DELAY.name,
                    )
                ],
            }
        ),
        shared_todos=[form],
        shared_documents=[
            _document(
                "recognized", todo=form, storage_path="drive-2", file_name=FORM.name
            )
        ],
    )


async def test_行程寄出_附件取自Drive_回寄件備份的連結() -> None:
    mail = FakeGoogleApi({"drive-1": DELAY, "drive-2": FORM})
    packager = GmailClaimPackager(google_api=mail)
    trip = await _ready_trip()
    package = _filled(await packager.build_trip(trip))

    result = await packager.send_trip(
        trip, package, "claims@example.com", USER, FakeGoogleAccess(*BOTH)
    )

    assert result.status == "sent"
    assert result.message_id == "message-1"
    assert result.gmail_url and "#sent/message-1" in result.gmail_url
    [(_, message)] = mail.sent
    assert message["To"] == "claims@example.com"
    assert [a.get_filename() for a in message.iter_attachments()] == [
        DELAY.name,
        FORM.name,
    ]


async def test_行程建立草稿_有空格也能建_回草稿連結() -> None:
    mail = FakeGoogleApi({"drive-1": DELAY, "drive-2": FORM})
    packager = GmailClaimPackager(google_api=mail)
    trip = await _ready_trip()
    package = await packager.build_trip(trip)

    result = await packager.send_trip(
        trip, package, None, USER, FakeGoogleAccess(*BOTH), mode="draft"
    )

    assert result.status == "drafted"
    assert result.external_ref == "draft-1"
    assert result.gmail_url and "#drafts?compose=draft-message-1" in result.gmail_url
    assert mail.sent == []


async def test_行程寄出_信裡還有空格就不寄() -> None:
    mail = FakeGoogleApi({"drive-1": DELAY, "drive-2": FORM})
    packager = GmailClaimPackager(google_api=mail)
    trip = await _ready_trip()
    package = await packager.build_trip(trip)

    result = await packager.send_trip(
        trip, package, "claims@example.com", USER, FakeGoogleAccess(*BOTH)
    )

    assert result.status == "failed"
    assert result.reason and "被保險人姓名" in result.reason
    assert mail.sent == []


async def test_行程寄出_沒有附件時只要Gmail授權() -> None:
    mail = FakeGoogleApi()
    packager = GmailClaimPackager(google_api=mail)
    trip = _trip(_delay())
    package = _filled(await packager.build_trip(trip))

    result = await packager.send_trip(
        trip, package, "claims@example.com", USER, FakeGoogleAccess("gmail.compose")
    )

    assert result.status == "sent"
