"""7 號同時打 Drive：寄出前下載附件、預覽時檢查檔案狀態，都同時做（有上限）、結果照原本順序。

用會等待的假 Google 轉接器量同時在跑的數量，不連 Google。
"""

import asyncio
import uuid
from email.message import EmailMessage

from app.agents.package import DriveFile, GmailClaimPackager, GoogleApiError
from app.agents.package.google_api import DriveFileStatus
from app.agents.package.packager import _PARALLEL
from app.contracts import AttachmentRef, ClaimPackage, DocumentView

from .test_send_gmail import BOTH, USER, FakeGoogleAccess, FakeGoogleApi, _ctx


class SlowGoogleApi(FakeGoogleApi):
    """每次讀 Drive 都等一下；記下最多同時有幾個在跑。編號越前面的檔等越久，讓後面的先完成。"""

    def __init__(self, files: dict[str, DriveFile]) -> None:
        super().__init__(files)
        self.running = 0
        self.peak = 0
        self.errors: dict[str, GoogleApiError] = {}

    async def _wait(self, file_id: str) -> None:
        self.running += 1
        self.peak = max(self.peak, self.running)
        order = list(self.files).index(file_id) if file_id in self.files else 0
        await asyncio.sleep(0.01 * (len(self.files) - order))
        self.running -= 1

    async def fetch_file(self, token: str, file_id: str) -> DriveFile:
        await self._wait(file_id)
        if file_id in self.errors:
            raise self.errors[file_id]
        return await super().fetch_file(token, file_id)

    async def file_status(self, token: str, file_id: str) -> DriveFileStatus:
        await self._wait(file_id)
        if file_id in self.errors:
            raise self.errors[file_id]
        return await super().file_status(token, file_id)


def _files(n: int) -> dict[str, DriveFile]:
    return {
        f"file-{i}": DriveFile(
            name=f"文件_{i}.pdf",
            mime_type="application/pdf",
            content=f"%PDF {i}".encode(),
        )
        for i in range(n)
    }


def _package(ids: list[str]) -> ClaimPackage:
    return ClaimPackage(
        subject="旅遊保險理賠申請",
        body="您好：\n聯絡電話：0912345678",
        attachments=[
            AttachmentRef(
                document_id=uuid.uuid4(), file_name=f"{i}.pdf", storage_path=i
            )
            for i in ids
        ],
    )


async def _send(api: FakeGoogleApi, package: ClaimPackage):
    return await GmailClaimPackager(google_api=api).send(
        _ctx(), package, "claims@example.com", USER, FakeGoogleAccess(*BOTH)
    )


def _attached(message: EmailMessage) -> list[bytes]:
    return [bytes(part.get_content()) for part in message.iter_attachments()]


async def test_附件同時下載_最多同時四份() -> None:
    api = SlowGoogleApi(_files(10))
    result = await _send(api, _package(list(api.files)))

    assert result.status == "sent"
    assert 1 < api.peak <= _PARALLEL


async def test_同時下載時後面的先完成_信裡的附件仍照送件包的順序() -> None:
    api = SlowGoogleApi(_files(6))
    ids = list(api.files)
    await _send(api, _package(ids))

    _, message = api.sent[0]
    assert _attached(message) == [f"%PDF {i}".encode() for i in range(6)]


async def test_同時下載時找不到與在垃圾桶的附件全部收齊後一起列出() -> None:
    api = SlowGoogleApi(_files(5))
    api.trashed.add("file-1")
    package = _package(["file-0", "file-1", "missing", "file-3"])

    result = await _send(api, package)

    assert result.status == "failed"
    assert result.reason and "missing.pdf" in result.reason
    assert "file-1.pdf" in result.reason
    assert api.sent == []


async def test_同時下載時有一份授權失效_回failed請重新授權_不寄出() -> None:
    api = SlowGoogleApi(_files(5))
    api.errors["file-4"] = GoogleApiError("unauthorized")
    package = _package(["missing", *api.files])

    result = await _send(api, package)

    assert result.status == "failed"
    assert result.reason and "Drive" in result.reason
    assert api.sent == []


def _documents(ids: list[str]) -> list[DocumentView]:
    return [DocumentView.model_construct(id=uuid.uuid4(), storage_path=i) for i in ids]


async def test_預覽時同時檢查檔案狀態_最多同時四份_找不到與在垃圾桶都對應到原本的文件() -> (
    None
):
    api = SlowGoogleApi(_files(8))
    api.trashed.add("file-2")
    documents = _documents([*api.files, "missing"])

    result = await GmailClaimPackager(google_api=api)._unavailable(
        documents, FakeGoogleAccess(*BOTH), USER
    )

    assert 1 < api.peak <= _PARALLEL
    assert result == {documents[2].id: "trashed", documents[8].id: "not_found"}


async def test_預覽時檢查檔案有一份連不上_當作都還在() -> None:
    api = SlowGoogleApi(_files(6))
    api.trashed.add("file-0")
    api.errors["file-5"] = GoogleApiError("unavailable")

    result = await GmailClaimPackager(google_api=api)._unavailable(
        _documents(list(api.files)), FakeGoogleAccess(*BOTH), USER
    )

    assert result == {}
