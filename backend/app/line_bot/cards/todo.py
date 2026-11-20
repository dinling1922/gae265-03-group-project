"""「待辦」進度卡（ADR-0035、FLEX-3）：每個行程一張卡，顯示完成度、事件與待辦，附「到網頁處理」按鈕。

資料與數字全部原樣取自 `list_todos` 的輸出（done／total 沿用工具的規則，不重算，ADR-0004）；
LINE 仍然只能查、不能改，要勾選或補文件請按按鈕回網頁。降級成純文字時用 `commands.todo.format_todos`。

上限：一個行程一張 bubble，多個行程放輪播（最多 12 張）；每張卡最多列 `ITEMS_PER_CARD` 項待辦，
超過的註明「還有 N 項，請到網頁查看」；行程超過 12 個時，前 11 個各一張，最後一張說明還有幾個行程。
整則 JSON 仍超過 50 KB 時，逐步減少每張卡列的項目數，最後仍超過才由 `reply_flex` 降級純文字。

「到網頁處理」的網址由 `PUBLIC_BASE_URL` 組成：只有一個行程時指到該行程頁 `/trip/<行程 id>`，
否則指到行程列表 `/trip`；沒有設定 `PUBLIC_BASE_URL` 時不顯示按鈕。
文案與版面都是暫定，要請設計者確認。
"""

from typing import Any

from app.core.config import settings
from app.line_bot import flex
from app.line_bot.commands.todo import (
    DONE_NONE_TEXT,
    DONE_TITLE,
    PENDING_NONE_PARTIAL_TEXT,
    PENDING_NONE_TEXT,
    PENDING_TITLE,
    PHASE_LABELS,
    SHARED_TITLE,
    UNKNOWN_DESTINATION,
    UNKNOWN_INCIDENT,
)

# ---- 暫定文案與版面（待設計者確認） ----
BUTTON_WEB = "到網頁處理"
MORE_ITEMS_TEXT = "…還有 {n} 項，請到網頁查看全部"
MORE_TRIPS_TITLE = "還有其他行程"
MORE_TRIPS_TEXT = "還有 {n} 個行程沒有列出，請到網頁查看全部。"
ITEMS_PER_CARD = 15
MAX_ITEMS_STEPS = (ITEMS_PER_CARD, 8, 4, 2, 0)
MAX_NAME = 40  # 標題與待辦文字的長度上限，避免單一欄位把卡片撐爆


def _cut(value: str, limit: int = MAX_NAME) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def web_url(trip_id: str | None = None) -> str | None:
    """網頁待辦所在的網址；沒設定 `PUBLIC_BASE_URL` 回 None。"""
    base = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/trip/{trip_id}" if trip_id else f"{base}/trip"


def _web_buttons(trip_id: str | None) -> list[flex.FlexNode]:
    url = web_url(trip_id)
    return [flex.uri_button(BUTTON_WEB, url, style="primary")] if url else []


def _trip_title(trip: dict[str, Any]) -> str:
    return _cut(trip["destination"] or UNKNOWN_DESTINATION)


def _trip_body(
    trip: dict[str, Any], *, done_view: bool, max_items: int
) -> list[flex.FlexNode]:
    dates = ""
    if trip["start_date"] and trip["end_date"]:
        dates = f"{trip['start_date']}～{trip['end_date']}"
    body: list[flex.FlexNode] = []
    if dates:
        body.append(flex.text(dates, size="xs", color=flex.MUTED))
    body.append(
        flex.text(
            f"已完成 {trip['done']}／{trip['total']}",
            size="md",
            weight="bold",
            color=flex.BRAND,
        )
    )
    groups: list[tuple[str, list[dict[str, Any]]]] = [
        (
            _cut(
                (g["incident_type"] or UNKNOWN_INCIDENT)
                + (f"（{g['occurred_date']}）" if g["occurred_date"] else "")
            ),
            g["todos"],
        )
        for g in trip["incidents"]
    ]
    if trip["shared_documents"]:
        groups.append((SHARED_TITLE, trip["shared_documents"]))
    listed = 0
    hidden = 0
    shown_any = False
    for name, todos in groups:
        if not todos:
            continue
        room = max_items - listed
        if room <= 0:
            hidden += len(todos)
            continue
        shown_any = True
        body.append(flex.text(f"▍{name}", weight="bold", margin="lg"))
        for todo in todos[:room]:
            phase = PHASE_LABELS.get(todo["phase"], todo["phase"])
            body.append(
                flex.text(
                    f"‧ {_cut(todo['title'])}（{phase}）", color=flex.INK, margin="sm"
                )
            )
        listed += min(room, len(todos))
        hidden += max(0, len(todos) - room)
    if hidden:
        body.append(
            flex.text(
                MORE_ITEMS_TEXT.format(n=hidden),
                size="xs",
                color=flex.MUTED,
                margin="lg",
            )
        )
    elif not shown_any and not any(g[1] for g in groups):
        body.append(
            flex.text(
                _empty_text(trip, done_view), size="sm", color=flex.MUTED, margin="lg"
            )
        )
    return body


def _empty_text(trip: dict[str, Any], done_view: bool) -> str:
    if done_view:
        return DONE_NONE_TEXT
    if trip["total"] > 0 and trip["done"] == trip["total"]:
        return PENDING_NONE_TEXT
    return PENDING_NONE_PARTIAL_TEXT


def _has_listed_todos(trip: dict[str, Any]) -> bool:
    return bool(trip["shared_documents"]) or any(g["todos"] for g in trip["incidents"])


def visible_trips(content: dict[str, Any]) -> list[dict[str, Any]]:
    """待辦卡要列的行程：略過沒有待辦的行程（FLEX-6，#560）。

    未完成：略過 `total` 為 0 的行程（沒有任何待辦）；`total > 0` 但全部完成的行程保留，
    卡上寫「全部完成了」。已完成：略過沒有已完成待辦可列的行程，不送空卡。
    `done／total` 原樣取自工具輸出，不重算（ADR-0004）。"""
    trips: list[dict[str, Any]] = content["trips"]
    if content["status"] == "done":
        return [t for t in trips if _has_listed_todos(t)]
    return [t for t in trips if t["total"] > 0]


def _build(content: dict[str, Any], max_items: int) -> flex.FlexNode:
    done_view = content["status"] == "done"
    kind = DONE_TITLE if done_view else PENDING_TITLE
    trips = visible_trips(content)
    shown = trips
    if len(trips) > flex.MAX_CAROUSEL_BUBBLES:
        shown = trips[: flex.MAX_CAROUSEL_BUBBLES - 1]
    bubbles = [
        flex.bubble(
            title=f"{kind}｜{_trip_title(trip)}",
            body=_trip_body(trip, done_view=done_view, max_items=max_items),
            buttons=_web_buttons(trip["trip_id"] if len(trips) == 1 else None),
        )
        for trip in shown
    ]
    if len(shown) < len(trips):
        bubbles.append(
            flex.bubble(
                title=MORE_TRIPS_TITLE,
                body=[flex.text(MORE_TRIPS_TEXT.format(n=len(trips) - len(shown)))],
                buttons=_web_buttons(None),
            )
        )
    return bubbles[0] if len(bubbles) == 1 else flex.carousel(bubbles)


def todo_card(content: dict[str, Any]) -> flex.FlexNode:
    """組待辦卡；整則超過 50 KB 時逐步減少每張卡列出的項目數。"""
    card: flex.FlexNode = {}
    for max_items in MAX_ITEMS_STEPS:
        card = _build(content, max_items)
        try:
            flex.check_contents(card)
        except flex.FlexError:
            continue
        return card
    return card  # 仍超過：交給 `reply_flex` 降級純文字
