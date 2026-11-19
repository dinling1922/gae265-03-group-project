"""LINE 圖文選單（Rich Menu）管理（ADR-0035、FLEX-4）。

一次性管理腳本用的積木：驗證選單圖、組建立選單的請求、透過 `RichMenuClient` 建立／上傳／設為預設／清掉舊的。
和回覆用的 `LineClient` 分開（那個只管回覆訊息）。選單圖不放在 repo：由設計者交付，用腳本參數指定路徑。
只用標準函式庫讀圖片檔頭，不為此加 Pillow。
"""

import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.line_bot import flex

API_BASE = "https://api.line.me/v2/bot"
DATA_API_BASE = "https://api-data.line.me/v2/bot"

MENU_NAME = "好險有你 主選單"  # 重跑時靠名稱找舊選單
CHAT_BAR_TEXT = "選單"  # 聊天室底部收合列的文字（LINE 限 14 字）
MAX_IMAGE_BYTES = 1_000_000  # LINE 限制 1 MB
IMAGE_SIZES = (
    (2500, 1686),
    (2500, 843),
)  # 本專案只用這兩種（LINE 另有較小的尺寸，不用）

# 暫定（待設計者確認）：按鈕順序與版面。大圖 2×2、小圖 1×4；順序都是由左到右、由上到下。
# 每個項目是 (名稱, 動作種類, postback 指令 cmd；uri 按鈕為 None)。不放「綁定」（要輸入綁定碼，點一下做不到）。
BUTTONS: tuple[tuple[str, str, str | None], ...] = (
    ("我的比較", "postback", "compare"),
    ("理賠待辦", "postback", "todo"),
    ("使用說明", "postback", "help"),
    ("開啟網站", "uri", None),
)


class RichMenuError(Exception):
    """腳本可預期的失敗（圖片不合規、設定沒填、LINE 拒絕）；訊息直接給使用者看。"""


# ---- 圖片驗證（只讀檔頭） ----


@dataclass(frozen=True)
class ImageInfo:
    content_type: str  # image/png 或 image/jpeg
    width: int
    height: int
    size: int


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # 填充
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # 沒有長度欄位的標記
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        # SOF0..SOF15（排除 DHT=C4、JPG=C8、DAC=CC）帶尺寸
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None


def validate_image(data: bytes) -> ImageInfo:
    """驗證格式（PNG／JPEG）、尺寸（2500×1686 或 2500×843）、大小（≤ 1 MB）；不合規丟 `RichMenuError`。"""
    size_text = "、".join(f"{w}×{h}" for w, h in IMAGE_SIZES)
    png = _png_size(data)
    jpeg = None if png else _jpeg_size(data)
    if png:
        content_type, dims = "image/png", png
    elif jpeg:
        content_type, dims = "image/jpeg", jpeg
    else:
        raise RichMenuError("圖片格式不對：只接受 PNG 或 JPEG。")
    if dims not in IMAGE_SIZES:
        raise RichMenuError(
            f"圖片尺寸是 {dims[0]}×{dims[1]}，要是 {size_text}（單位像素）。"
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise RichMenuError(
            f"圖片 {len(data) / 1_000_000:.2f} MB，超過 LINE 的上限 1 MB，請壓縮後再試。"
        )
    return ImageInfo(content_type, dims[0], dims[1], len(data))


# ---- 選單請求 ----


def build_menu_request(width: int, height: int, site_url: str) -> dict[str, Any]:
    """組建立選單的請求內容。版面依圖片尺寸：2500×1686 用 2×2、2500×843 用 1×4。"""
    if (width, height) not in IMAGE_SIZES:
        raise RichMenuError(f"不支援的選單尺寸 {width}×{height}。")
    columns = 2 if height == 1686 else 4
    rows = len(BUTTONS) // columns
    cell_w, cell_h = width // columns, height // rows
    areas: list[dict[str, Any]] = []
    for index, (label, kind, cmd) in enumerate(BUTTONS):
        action: dict[str, Any]
        if kind == "uri":
            action = {"type": "uri", "label": label, "uri": site_url}
        else:
            assert cmd is not None
            # 不填 displayText：按鈕不要在聊天室多出一句使用者沒打的話（ADR-0035）
            action = {
                "type": "postback",
                "label": label,
                "data": flex.postback_data(cmd),
            }
        areas.append(
            {
                "bounds": {
                    "x": (index % columns) * cell_w,
                    "y": (index // columns) * cell_h,
                    "width": cell_w,
                    "height": cell_h,
                },
                "action": action,
            }
        )
    return {
        "size": {"width": width, "height": height},
        "selected": True,  # 預設展開
        "name": MENU_NAME,
        "chatBarText": CHAT_BAR_TEXT,
        "areas": areas,
    }


# ---- LINE API 介面 ----


class RichMenuClient(Protocol):
    def create(self, request: dict[str, Any]) -> str:
        """建立選單，回傳 richMenuId。"""
        ...

    def upload_image(
        self, rich_menu_id: str, image: bytes, content_type: str
    ) -> None: ...

    def set_default(self, rich_menu_id: str) -> None:
        """設為所有使用者的預設選單。"""
        ...

    def list_menus(self) -> list[dict[str, Any]]:
        """列出所有選單（每筆至少有 richMenuId、name）。"""
        ...

    def delete(self, rich_menu_id: str) -> None: ...


class HttpRichMenuClient:
    def __init__(self, channel_access_token: str, timeout: float = 30.0) -> None:
        self._headers = {"Authorization": f"Bearer {channel_access_token}"}
        self._timeout = timeout

    def _send(self, method: str, url: str, what: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = httpx.request(
                method, url, headers=headers, timeout=self._timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            raise RichMenuError(f"{what}：連線失敗（{type(exc).__name__}）。") from exc
        if response.status_code != 200:
            # 只放狀態碼與 LINE 回的訊息，不放權杖
            raise RichMenuError(
                f"{what}：LINE 回 HTTP {response.status_code}：{response.text[:300]}"
            )
        return response

    def create(self, request: dict[str, Any]) -> str:
        response = self._send(
            "POST", f"{API_BASE}/richmenu", "建立圖文選單", json=request
        )
        return str(response.json()["richMenuId"])

    def upload_image(self, rich_menu_id: str, image: bytes, content_type: str) -> None:
        self._send(
            "POST",
            f"{DATA_API_BASE}/richmenu/{rich_menu_id}/content",
            "上傳選單圖",
            content=image,
            headers={"Content-Type": content_type},
        )

    def set_default(self, rich_menu_id: str) -> None:
        self._send(
            "POST", f"{API_BASE}/user/all/richmenu/{rich_menu_id}", "設為預設選單"
        )

    def list_menus(self) -> list[dict[str, Any]]:
        response = self._send("GET", f"{API_BASE}/richmenu/list", "列出圖文選單")
        return list(response.json().get("richmenus", []))

    def delete(self, rich_menu_id: str) -> None:
        self._send("DELETE", f"{API_BASE}/richmenu/{rich_menu_id}", "刪除圖文選單")


# ---- 流程 ----


@dataclass
class DeployResult:
    new_id: str
    deleted_ids: list[str] = field(default_factory=list)
    failed_deletes: list[str] = field(default_factory=list)


def deploy(
    client: RichMenuClient,
    image: bytes,
    site_url: str,
    log: Callable[[str], None] = lambda _: None,
) -> DeployResult:
    """建立新選單、上傳圖片、設為預設，成功後才刪掉同名舊選單（任何時候都有一個可用的選單）。

    新選單建立後若上傳或設預設失敗，會把這個半成品刪掉再重新丟例外，不留垃圾；舊的原封不動。
    """
    info = validate_image(image)
    request = build_menu_request(info.width, info.height, site_url)
    old_ids = [
        str(m["richMenuId"]) for m in client.list_menus() if m.get("name") == MENU_NAME
    ]
    new_id = client.create(request)
    log(f"已建立圖文選單 {new_id}")
    try:
        client.upload_image(new_id, image, info.content_type)
        log("已上傳選單圖")
        client.set_default(new_id)
        log("已設為所有使用者的預設選單")
    except RichMenuError:
        try:
            client.delete(new_id)
        except RichMenuError:
            pass
        raise
    result = DeployResult(new_id)
    for old_id in old_ids:
        try:
            client.delete(old_id)
            result.deleted_ids.append(old_id)
            log(f"已刪除舊選單 {old_id}")
        except RichMenuError as exc:
            # 新選單已生效；舊的刪不掉只提醒，重跑會再試
            result.failed_deletes.append(old_id)
            log(f"警告：舊選單 {old_id} 刪除失敗（{exc}），重跑腳本會再試一次")
    return result


# ---- 佔位圖（只供本機驗證，不是正式資源） ----


def make_placeholder_png(width: int = 2500, height: int = 1686) -> bytes:
    """純標準函式庫產生一張棋盤格分區的 PNG。不含文字、也不是設計稿，不可上線。"""
    columns = 2 if height == 1686 else 4
    rows = len(BUTTONS) // columns
    cell_w, cell_h = width // columns, height // rows
    palette = [(214, 220, 229), (186, 196, 210)]  # 兩色，看得出分區
    raw = bytearray()
    for y in range(height):
        cells = [
            bytes(palette[(col + y // cell_h) % 2]) * cell_w for col in range(columns)
        ]
        raw += b"\x00" + b"".join(cells)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
