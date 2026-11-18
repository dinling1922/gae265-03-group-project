"""7 號真的連 Google 的轉接器：Drive v3 取檔、Gmail API 寄信與建立草稿（規格「模組」）。

只做薄薄一層 HTTP 呼叫，把 Google 的錯誤換成錯誤類別。所有呼叫共用同一個連線池，
不用每次重新連線（TLS 握手）；授權依每次呼叫放在 header（Drive 與 Gmail 的 token 不同）。`transport` 給測試換成假的網路，
不寫連真的 Google 的自動測試（ADR-0020），真的寄送用測試帳號手動確認。
"""

import asyncio
from typing import Any, cast
from urllib.parse import quote

import httpx

from app.agents.package.google_api import (
    DriveFile,
    DriveFileStatus,
    GmailDraft,
    GoogleApiError,
    GoogleErrorKind,
)

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
GMAIL_SEND = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send"
GMAIL_DRAFTS = "https://gmail.googleapis.com/upload/gmail/v1/users/me/drafts"


def _kind(status_code: int) -> GoogleErrorKind:
    if status_code == 404:
        return "not_found"
    if status_code in (401, 403):
        return "unauthorized"
    return "unavailable"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class HttpGoogleApi:
    def __init__(
        self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        self._shared: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _http(self) -> httpx.AsyncClient:
        # 同一個事件迴圈共用一個 client（連線池）；換了事件迴圈（例如測試）就重開一個，
        # 不把別的迴圈建立的連線拿來用
        loop = asyncio.get_running_loop()
        if self._shared is None or self._loop is not loop or self._shared.is_closed:
            self._shared = httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            )
            self._loop = loop
        return self._shared

    async def file_status(self, token: str, file_id: str) -> DriveFileStatus:
        url = f"{DRIVE_FILES}/{quote(file_id, safe='')}"
        try:
            metadata = await self._http().get(
                url, params={"fields": "trashed"}, headers=_auth(token)
            )
            if metadata.status_code == 404:
                return "not_found"
            if metadata.is_error:
                raise GoogleApiError(_kind(metadata.status_code))
            return "trashed" if metadata.json().get("trashed") is True else "ok"
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
            raise GoogleApiError("unavailable") from error

    async def fetch_file(self, token: str, file_id: str) -> DriveFile:
        # 檔案編號整段編碼，避免奇怪的編號改到別的網址
        url = f"{DRIVE_FILES}/{quote(file_id, safe='')}"
        try:
            client = self._http()
            metadata = await client.get(
                url, params={"fields": "name,mimeType,trashed"}, headers=_auth(token)
            )
            if metadata.is_error:
                raise GoogleApiError(_kind(metadata.status_code))
            info = metadata.json()
            if info.get("trashed") is True:
                raise GoogleApiError("trashed")
            media = await client.get(url, params={"alt": "media"}, headers=_auth(token))
            if media.is_error:
                raise GoogleApiError(_kind(media.status_code))
            return DriveFile(
                name=str(info["name"]),
                mime_type=str(info.get("mimeType") or "application/octet-stream"),
                content=media.content,
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            # 錯誤訊息只放錯誤類別，不放 token 或檔案內容
            raise GoogleApiError("unavailable") from error

    async def send_message(self, token: str, message: bytes) -> str:
        data = await self._upload(GMAIL_SEND, token, message)
        try:
            return str(data["id"])
        except (KeyError, TypeError) as error:
            raise GoogleApiError("unavailable") from error

    async def create_draft(self, token: str, message: bytes) -> GmailDraft:
        # drafts.create 回草稿編號（id）與草稿裡那封信（message.id）；Gmail 網頁打開草稿要用信件編號
        data = await self._upload(GMAIL_DRAFTS, token, message)
        try:
            return GmailDraft(id=str(data["id"]), message_id=str(data["message"]["id"]))
        except (KeyError, TypeError) as error:
            raise GoogleApiError("unavailable") from error

    async def _upload(self, url: str, token: str, message: bytes) -> dict[str, Any]:
        # 用上傳的寫法把整封信原樣送出：附件大也寄得出去，不用轉 base64 放進 JSON
        try:
            response = await self._http().post(
                url,
                params={"uploadType": "media"},
                headers={**_auth(token), "Content-Type": "message/rfc822"},
                content=message,
            )
            if response.is_error:
                kind = _kind(response.status_code)
                raise GoogleApiError("unavailable" if kind == "not_found" else kind)
            data = response.json()
            if not isinstance(data, dict):
                raise GoogleApiError("unavailable")
            return cast(dict[str, Any], data)
        except (httpx.HTTPError, ValueError) as error:
            raise GoogleApiError("unavailable") from error
