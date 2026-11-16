"""6 號行程版（契約 v2.13，ADR-0029）：行程頁一個上傳框，文件在整個行程的待辦裡找。

用日本 demo：班機延誤（D01）與東京就醫（D13、D14、D25）兩個事件，共同文件 D02、D10、D24。
Azure、Drive 都是假的；範例文件由 samples/make_samples.py 產生。
"""

import uuid
from datetime import UTC, date, datetime

from app.agents.document.walkthrough import sample_file
from app.contracts import (
    DocumentView,
    IncidentContext,
    PolicySummary,
    TodoView,
    TripContext,
    TripDocumentDiscarder,
    TripDocumentRechecker,
    TripDocumentRecognizer,
    TripDocumentRenamer,
)

from tests.agents.document_helpers import USER, FakeDrive, FakeGoogle, agent, upload

POLICY = PolicySummary(
    plan_id="chubb_advanced",
    plan_name="安達 進階型",
    start_date=date(2026, 7, 5),
    end_date=date(2026, 7, 11),
    insurance_codes=[],
    coverages=[],
)


def _todo(code: str, title: str, incident_id: uuid.UUID | None) -> TodoView:
    return TodoView(
        id=uuid.uuid4(),
        phase="on_site",
        title=title,
        document_code=code,
        incident_id=incident_id,
        scope="incident" if incident_id else "policy",
    )


def _incident(
    type_code: str, type_name: str, day: int, todos: dict[str, str]
) -> IncidentContext:
    incident_id = uuid.uuid4()
    return IncidentContext(
        incident_id=incident_id,
        title=type_name,
        description="",
        status="open",
        destination="日本",
        occurred_at=datetime(2026, 7, day, 3, 0, tzinfo=UTC),
        incident_type_code=type_code,
        incident_type_name=type_name,
        policy=POLICY,
        todos=[_todo(code, title, incident_id) for code, title in todos.items()],
    )


def japan_trip() -> TripContext:
    return TripContext(
        policy=POLICY,
        incidents=[
            _incident(
                "medical",
                "生病或受傷就醫",
                8,
                {
                    "D13": "請醫院開立診斷書",
                    "D14": "保留醫療費用收據正本",
                    "D25": "保險單或其謄本",
                },
            ),
            _incident(
                "flight_delay",
                "班機延誤",
                10,
                {"D01": "請航空公司開立班機延誤／取消證明"},
            ),
        ],
        shared_todos=[
            _todo("D02", "機票及登機證，或航空業者出具的搭機證明", None),
            _todo("D10", "填寫理賠申請書", None),
            _todo("D24", "準備受益人身分證明", None),
        ],
    )


def todo_of(trip: TripContext, code: str) -> TodoView:
    return next(t for t in trip.todos if t.document_code == code)


def sample(name: str):
    content, content_type = sample_file(name)
    return upload(content, f"{name}.pdf", content_type)


def test_document_agent_implements_trip_interfaces() -> None:
    a = agent()
    assert isinstance(a, TripDocumentRecognizer)
    assert isinstance(a, TripDocumentRechecker)
    assert isinstance(a, TripDocumentDiscarder)
    assert isinstance(a, TripDocumentRenamer)


async def test_each_document_finds_its_todo_across_incidents() -> None:
    trip, drive = japan_trip(), FakeDrive()
    for name, code in (
        ("delay", "D01"),
        ("med_certificate", "D13"),
        ("med_receipt", "D14"),
        ("med_policy", "D25"),
        ("med_passport_copy", "D24"),
        ("med_claim_form", "D10"),
    ):
        result = await agent(drive=drive).recognize_trip(
            trip, sample(name), USER, FakeGoogle()
        )
        assert result.status == "recognized", (name, result.reason)
        assert result.matched_todo_id == todo_of(trip, code).id, name


async def test_file_names_and_folders_follow_the_todo_owner() -> None:
    trip, drive = japan_trip(), FakeDrive()
    names = {}
    for name in ("delay", "med_certificate", "med_passport_copy", "med_claim_form"):
        result = await agent(drive=drive).recognize_trip(
            trip, sample(name), USER, FakeGoogle()
        )
        assert result.storage_path is not None
        names[name] = (result.stored_name, drive.files[result.storage_path][0])
    folder = {name: fid for (name, _), fid in drive.folders.items()}
    trip_folder = folder["日本 2026-07-05～07-11"]
    assert names["delay"] == (
        "班機延誤_航空公司開立班機延誤／取消證明_1.pdf",
        folder["班機延誤 2026-07-10"],
    )
    assert names["med_certificate"] == (
        "生病或受傷就醫_醫院開立診斷書_1.pdf",
        folder["生病或受傷就醫 2026-07-08"],
    )
    # 共同文件不屬於任何事件：檔名「共同文件_…」，和理賠申請書一樣放在行程資料夾
    assert names["med_passport_copy"] == (
        "共同文件_受益人身分證明_1.pdf",
        trip_folder,
    )
    assert names["med_claim_form"][1] == trip_folder


async def test_same_kind_in_two_incidents_asks_the_user() -> None:
    trip = japan_trip()
    second = _incident("medical", "生病或受傷就醫", 9, {"D13": "請醫院開立診斷書"})
    trip = trip.model_copy(update={"incidents": [*trip.incidents, second]})
    result = await agent().recognize_trip(
        trip, sample("med_certificate"), USER, FakeGoogle()
    )
    assert result.status == "needs_review"
    assert result.matched_todo_id is None
    assert set(result.candidate_todo_ids) == {
        t.id for t in trip.todos if t.document_code == "D13"
    }
    assert result.storage_path is not None  # 先存檔，使用者選了待辦平台沿用


async def test_target_todo_in_another_incident_is_respected() -> None:
    trip = japan_trip()
    target = todo_of(trip, "D01")
    file = sample("med_certificate").model_copy(update={"target_todo_id": target.id})
    result = await agent().recognize_trip(trip, file, USER, FakeGoogle())
    assert result.status == "needs_review"  # 對不上使用者選的待辦，不改對別的
    assert result.matched_todo_id is None


async def test_trip_without_incidents_matches_nothing() -> None:
    trip = japan_trip().model_copy(update={"incidents": []})
    result = await agent().recognize_trip(trip, sample("delay"), USER, FakeGoogle())
    assert (result.status, result.failure) == ("failed", "not_matched")


async def test_rename_trip_moves_shared_document_to_trip_folder() -> None:
    trip, drive = japan_trip(), FakeDrive()
    pending = await agent(drive=drive).recognize_trip(
        trip,
        sample("med_passport_copy").model_copy(
            update={"target_todo_id": todo_of(trip, "D25").id}
        ),
        USER,
        FakeGoogle(),
    )
    assert pending.status == "needs_review" and pending.storage_path
    document = DocumentView(
        id=uuid.uuid4(),
        file_name=pending.stored_name or "",
        content_type="application/pdf",
        storage_path=pending.storage_path,
        recognition_status="needs_review",
    )
    name = await agent(drive=drive).rename_trip(
        trip, document, todo_of(trip, "D24"), USER, FakeGoogle()
    )
    assert name == "共同文件_受益人身分證明_1.pdf"
    folder = {n: fid for (n, _), fid in drive.folders.items()}
    assert drive.files[pending.storage_path][0] == folder["日本 2026-07-05～07-11"]


async def test_recheck_trip_uses_the_chosen_todo() -> None:
    trip, drive = japan_trip(), FakeDrive()
    pending = await agent(drive=drive).recognize_trip(
        trip,
        sample("delay").model_copy(update={"target_todo_id": todo_of(trip, "D13").id}),
        USER,
        FakeGoogle(),
    )
    assert pending.storage_path
    document = DocumentView(
        id=uuid.uuid4(),
        file_name=pending.stored_name or "",
        content_type="application/pdf",
        storage_path=pending.storage_path,
        recognition_status="needs_review",
    )
    result = await agent(drive=drive).recheck_trip(
        trip, document, todo_of(trip, "D01"), USER, FakeGoogle()
    )
    assert result.status == "recognized"
    assert result.matched_todo_id == todo_of(trip, "D01").id
    assert result.stored_name == "班機延誤_航空公司開立班機延誤／取消證明_1.pdf"


async def test_discard_trip_deletes_the_file() -> None:
    trip, drive = japan_trip(), FakeDrive()
    drive.files["drive-file-9"] = ("folder", "待確認_1.pdf", b"x")
    document = DocumentView(
        id=uuid.uuid4(),
        file_name="待確認_1.pdf",
        content_type="application/pdf",
        storage_path="drive-file-9",
        recognition_status="needs_review",
    )
    assert await agent(drive=drive).discard_trip(trip, document, USER, FakeGoogle())
    assert "drive-file-9" not in drive.files


async def test_pending_shared_document_goes_to_trip_folder() -> None:
    """不確定、但最可能是共同文件時：暫存檔直接放行程資料夾，不另開「共同文件 日期」資料夾。"""
    trip, drive = japan_trip(), FakeDrive()
    pending = await agent(drive=drive).recognize_trip(
        trip,
        sample("delay").model_copy(update={"target_todo_id": todo_of(trip, "D24").id}),
        USER,
        FakeGoogle(),
    )
    assert pending.status == "needs_review" and pending.storage_path
    folder = {n: fid for (n, _), fid in drive.folders.items()}
    assert drive.files[pending.storage_path][0] == folder["日本 2026-07-05～07-11"]
    assert not any(n.startswith("共同文件") for n in folder)
