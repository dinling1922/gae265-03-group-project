"""Flex 訊息的共用積木與上限檢查（ADR-0035）。

這裡只放「怎麼排版」的小積木與 LINE 的限制檢查，不放任何一張卡的內容；每張卡放在 `cards/` 自己的檔案，
用這些積木組出 bubble／carousel，再交給 `LineContext.reply_flex`。內容只能來自既有文字或 MCP 工具輸出，
不查資料庫、不產生數字（ADR-0004、ADR-0033）。

LINE 的限制（超過時 `LineContext.reply_flex` 會降級成純文字，不會讓使用者沒回應）：
單則 Flex JSON 50 KB、輪播最多 12 張 bubble、一次回覆最多 5 則、postback data 300 字。

postback data 格式：`cmd=<指令>&<鍵>=<值>…`（URL query 格式）。只放指令與識別碼，不放個資。
例如 `cmd=compare&id=<比較紀錄 uuid>`；建立用 `postback_data`，解析用 `parse_postback_data`。
"""

import json
from collections.abc import Sequence
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode

# ---- LINE 的限制 ----
# 單則上限 50 KB，這裡保守用 50,000 位元組
MAX_FLEX_BYTES = 50_000
MAX_CAROUSEL_BUBBLES = 12
MAX_MESSAGES_PER_REPLY = 5
MAX_POSTBACK_DATA = 300
MAX_BUTTON_LABEL = 20

# ---- 好險有你品牌色（取自 frontend/src/styles.css 的設計代幣）----
BRAND = "#285442"
HEADING = "#183b20"
INK = "#202820"
MUTED = "#647364"
LEAF_SOFT = "#f2f8e7"
LINE_COLOR = "#e2e7dd"
PAPER = "#fbfaf6"

FlexNode = dict[str, Any]
ButtonStyle = Literal["primary", "secondary", "link"]


class FlexError(ValueError):
    """Flex 組裝或上限檢查失敗；`reply_flex` 會接住並降級成純文字。"""


# ---- postback data ----


def postback_data(cmd: str, **params: str) -> str:
    """組 postback data：`cmd=compare&id=...`。超過 300 字（LINE 的限制）丟 `FlexError`。"""
    data = urlencode([("cmd", cmd), *params.items()])
    if len(data) > MAX_POSTBACK_DATA:
        raise FlexError(f"postback data 超過 {MAX_POSTBACK_DATA} 字")
    return data


def parse_postback_data(data: object) -> dict[str, str] | None:
    """解析 postback data；格式不對或沒有 `cmd` 回 None（不丟例外，內容來自外部）。"""
    if not isinstance(data, str) or not data or len(data) > MAX_POSTBACK_DATA:
        return None
    params = dict(parse_qsl(data, keep_blank_values=True))
    return params if params.get("cmd") else None


# ---- 積木 ----


def text(
    content: str,
    *,
    size: str = "sm",
    weight: str = "regular",
    color: str = INK,
    wrap: bool = True,
    margin: str | None = None,
) -> FlexNode:
    node: FlexNode = {
        "type": "text",
        "text": content,
        "size": size,
        "weight": weight,
        "color": color,
        "wrap": wrap,
    }
    if margin:
        node["margin"] = margin
    return node


def box(
    contents: Sequence[FlexNode],
    *,
    layout: str = "vertical",
    spacing: str = "sm",
    **extra: Any,
) -> FlexNode:
    return {
        "type": "box",
        "layout": layout,
        "spacing": spacing,
        "contents": list(contents),
        **extra,
    }


def postback_button(
    label: str, data: str, *, style: ButtonStyle = "secondary"
) -> FlexNode:
    """按下去觸發 postback：聊天室不會多出使用者沒打的字（不帶 displayText）。"""
    return _button({"type": "postback", "label": _label(label), "data": data}, style)


def uri_button(label: str, uri: str, *, style: ButtonStyle = "link") -> FlexNode:
    """開網址的按鈕（例如「到網頁處理」）。"""
    return _button({"type": "uri", "label": _label(label), "uri": uri}, style)


def _label(label: str) -> str:
    if not label or len(label) > MAX_BUTTON_LABEL:
        raise FlexError(f"按鈕文字要 1～{MAX_BUTTON_LABEL} 字：{label!r}")
    return label


def _button(action: dict[str, str], style: ButtonStyle) -> FlexNode:
    node: FlexNode = {
        "type": "button",
        "action": action,
        "style": style,
        "height": "sm",
    }
    if style == "primary":
        node["color"] = BRAND
    return node


def bubble(
    *,
    title: str | None = None,
    body: Sequence[FlexNode] = (),
    buttons: Sequence[FlexNode] = (),
) -> FlexNode:
    """一張卡：品牌色標題列、內容、底部按鈕。三者都可省略，但至少要有一個（否則 LINE 會拒絕）。"""
    node: FlexNode = {"type": "bubble"}
    if title:
        node["header"] = box(
            [text(title, size="lg", weight="bold", color="#ffffff")],
            backgroundColor=BRAND,
            paddingAll="lg",
        )
    if body:
        node["body"] = box(body, paddingAll="lg")
    if buttons:
        node["footer"] = box(buttons, paddingAll="md")
    if len(node) == 1:
        raise FlexError("bubble 沒有任何內容")
    return node


def carousel(bubbles: Sequence[FlexNode]) -> FlexNode:
    """輪播；超過 12 張丟 `FlexError`（呼叫端應先截斷並提示到網頁看，或讓 `reply_flex` 降級）。"""
    if not bubbles:
        raise FlexError("輪播沒有任何卡片")
    if len(bubbles) > MAX_CAROUSEL_BUBBLES:
        raise FlexError(f"輪播最多 {MAX_CAROUSEL_BUBBLES} 張，收到 {len(bubbles)} 張")
    return {"type": "carousel", "contents": list(bubbles)}


# ---- 上限檢查 ----


def check_contents(contents: FlexNode) -> None:
    """檢查單則 Flex：type 要是 bubble／carousel、輪播張數、JSON 大小。不合丟 `FlexError`。"""
    kind = contents.get("type")
    if kind not in ("bubble", "carousel"):
        raise FlexError(f"Flex contents 的 type 不對：{kind!r}")
    if kind == "carousel":
        count = len(contents.get("contents") or [])
        if not 1 <= count <= MAX_CAROUSEL_BUBBLES:
            raise FlexError(f"輪播張數 {count} 不在 1～{MAX_CAROUSEL_BUBBLES}")
    try:
        size = len(json.dumps(contents, ensure_ascii=False).encode())
    except (TypeError, ValueError) as exc:
        raise FlexError("Flex contents 無法轉成 JSON") from exc
    if size > MAX_FLEX_BYTES:
        raise FlexError(f"Flex JSON {size} 位元組，超過 {MAX_FLEX_BYTES}")
