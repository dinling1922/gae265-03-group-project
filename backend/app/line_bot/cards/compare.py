"""「比較」卡片（ADR-0035、FLEX-2）：比較紀錄輪播卡與推薦詳情卡。

內容全部取自 MCP 工具 `list_comparisons`／`get_comparison` 的輸出，這裡只負責排版，
不查資料庫、不重算任何數字（ADR-0004）。降級成純文字時，回 `commands/compare.py` 的 `format_list`／`format_detail`。

輪播每筆一張卡，「看推薦」按鈕的 postback 帶比較紀錄 id（`cmd=compare&id=<uuid>`），不是列表編號，
所以列表順序在按之前改變了，按到的仍是卡上那一筆。
"""

from typing import Any

from app.line_bot import flex

# ---- 暫定文案與版面（待設計者確認） ----
BUTTON_VIEW = "看推薦"
BUTTON_BACK = "回比較列表"
HAS_RECOMMENDATION = "已有推薦"
NO_RECOMMENDATION = "尚未推薦"
MORE_TITLE = "還有更早的紀錄"
MORE_TEXT = "這裡只放最近 {limit} 筆，更早的比較紀錄請到網頁查看。"
INCOMPLETE_NOTE = "這次比較還沒有推薦方案。"
INCOMPLETE_HINT = "請回網頁完成這一步，完成後再按「看推薦」。"
DISCLAIMER = "保額與保費是試算金額，不是保證的理賠上限；實際以網頁與保險條款為準。"


def _dates(item: dict[str, Any]) -> str:
    return f"{item['depart_date']}～{item['return_date']}（{item['days']} 天）"


def comparison_bubble(item: dict[str, Any]) -> flex.FlexNode:
    """輪播裡的一張：目的地、日期、天數、是否已有推薦，加「看推薦」按鈕。"""
    done = item["has_recommendation"]
    return flex.bubble(
        title=item["destination"],
        body=[
            flex.text(_dates(item), color=flex.INK),
            flex.text(
                HAS_RECOMMENDATION if done else NO_RECOMMENDATION,
                weight="bold",
                color=flex.BRAND if done else flex.MUTED,
                margin="md",
            ),
        ],
        buttons=[
            flex.postback_button(
                BUTTON_VIEW,
                flex.postback_data("compare", id=str(item["id"])),
                style="primary",
            )
        ],
    )


def more_bubble(limit: int) -> flex.FlexNode:
    """列表達到上限時放在最後的提示卡：到網頁看更多。"""
    return flex.bubble(
        title=MORE_TITLE,
        body=[flex.text(MORE_TEXT.format(limit=limit), color=flex.MUTED)],
    )


def compare_carousel(
    items: list[dict[str, Any]], *, more_limit: int | None = None
) -> flex.FlexNode:
    """`more_limit` 有值（列表已達上限，可能還有更多）時，最後加一張「到網頁看更多」。"""
    bubbles = [comparison_bubble(item) for item in items]
    if more_limit is not None:
        bubbles.append(more_bubble(more_limit))
    return flex.carousel(bubbles)


def _plan_block(label: str, plan: dict[str, Any]) -> list[flex.FlexNode]:
    return [
        flex.text(label, size="xs", color=flex.MUTED, margin="lg"),
        flex.text(
            f"{plan['name']}（{plan['company']}）", weight="bold", color=flex.HEADING
        ),
        flex.text(
            f"保額 {plan['sum_insured']:,} 元／保費 {plan['premium']:,} 元",
            color=flex.INK,
        ),
    ]


def detail_bubble(
    detail: dict[str, Any], missing_text: str | None = None
) -> flex.FlexNode:
    """推薦詳情卡。`missing_text` 是尚無推薦時要對使用者說的缺哪一步（由呼叫端從工具的 missing 轉成）。"""
    trip = detail["trip"]
    body = [flex.text(_dates(trip), color=flex.INK)]
    if detail["concerns"]:
        body.append(
            flex.text(
                "在意事項：" + "、".join(detail["concerns"]),
                size="xs",
                color=flex.MUTED,
                margin="md",
            )
        )
    if detail["status"] != "complete":
        body += [
            flex.text(INCOMPLETE_NOTE, weight="bold", margin="lg"),
            flex.text(missing_text or "", color=flex.MUTED),
            flex.text(INCOMPLETE_HINT, size="xs", color=flex.MUTED),
        ]
    else:
        body += _plan_block("推薦方案", detail["recommended"])
        body += _plan_block("次推薦", detail["alternative"])
        body.append(flex.text(DISCLAIMER, size="xs", color=flex.MUTED, margin="lg"))
    return flex.bubble(
        title=trip["destination"],
        body=body,
        buttons=[flex.postback_button(BUTTON_BACK, flex.postback_data("compare"))],
    )
