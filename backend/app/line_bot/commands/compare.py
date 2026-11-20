"""「比較」指令（ADR-0033、#489）：看最近的比較紀錄，再看某一筆的推薦方案。

- `比較`：呼叫 `list_comparisons`，回輪播卡（FLEX-2，一筆一張，按鈕「看推薦」帶比較紀錄 id）；
  降級成純文字時回列表（編號、目的地、日期、天數、是否已有推薦），快速回覆可點選其中一筆
- `比較 <編號>`（快速回覆按下去送出的文字也是這個格式）：以呼叫當下的最新列表取第 N 筆，再呼叫 `get_comparison`；
  維持純文字回覆（FLEX-2 決定不變），要看卡片請按輪播上的「看推薦」
- 卡片按鈕的 postback（`cmd=compare&id=<uuid>`）：只呼叫 `get_comparison`，回該筆的詳情卡（降級為 `format_detail` 純文字）；
  id 不是 uuid、不存在或不是本人的，一律回同一句「找不到」，不洩漏紀錄存在與否

編號 vs. 比較紀錄 id 前綴：選編號。使用者看得懂也打得出來；代價是列表順序會隨新增比較改變，
所以每次都用最新列表取第 N 筆，回覆的第一行一定寫出目的地與日期讓使用者核對是不是想看的那一筆。
id 前綴雖穩定，但那是使用者不認得的字串，快速回覆以外幾乎沒人會手打。

名稱與數字全部原樣取自 MCP 工具的輸出，轉接層不重算、不增加欄位（ADR-0004、ADR-0032）。
文案都是暫定，要請設計者確認。
"""

import uuid
from typing import Any

from app.line_bot import texts
from app.line_bot.cards.compare import compare_carousel, detail_bubble
from app.line_bot.client import QuickReply
from app.line_bot.failures import reply_if_not_success
from app.line_bot.limits import clamp_quick_replies, fit_text
from app.line_bot.mcp_client import McpFailure, McpSuccess, call_mcp_tool
from app.line_bot.registry import LineContext, line_command

# 列表最多幾筆：快速回覆上限是 13，取 10 與 `list_comparisons` 的預設一致，
# 也讓列表文字短（每筆一行）、留 3 個名額給「說明」之類的導覽
LIST_LIMIT = 10

# ---- 暫定文案（待設計者確認） ----
USAGE_TEXT = "請傳「比較」看最近的比較紀錄，再傳「比較 1」這樣的格式看第 1 筆（編號以最新列表為準）。"
EMPTY_TEXT = "你還沒有比較紀錄。請先到網頁完成一次旅遊保險比較，之後就能在這裡查看。"
LIST_HEADER = "最近的比較紀錄（最新在前，共 {count} 筆）："
LIST_MORE_NOTE = "（只列最近 {limit} 筆，更早的請到網頁查看）"
LIST_FOOTER = "點下面的按鈕，或傳「比較 1」看第 1 筆的推薦。編號以最新的列表為準，新增比較後順序會變。"
OUT_OF_RANGE_TEXT = (
    "沒有第 {n} 筆。目前有 {count} 筆，請傳「比較 1」到「比較 {count}」。"
)
OUT_OF_RANGE_BEYOND_TEXT = "沒有第 {n} 筆。這裡只列最近 {limit} 筆，請傳「比較 1」到「比較 {limit}」，更早的請到網頁查看。"
HAS_RECOMMENDATION = "已有推薦"
NO_RECOMMENDATION = "尚未推薦"
INCOMPLETE_HEADER = "這次比較還沒有推薦方案。"
INCOMPLETE_FOOTER = "請回網頁完成這一步，完成後再傳「比較」查看。"
MISSING_CONCERNS_TEXT = "還沒選在意事項。"
MISSING_RECOMMENDATION_TEXT = "還沒產生推薦方案。"
MISSING_STALE_TEXT = "推薦的方案已不在目前的報價中。"
MISSING_OTHER_TEXT = "這次比較還沒完成。"
NOT_FOUND_TEXT = "找不到這筆比較紀錄，可能已經刪除。請傳「比較」看最新的列表。"
DISCLAIMER = "保額與保費是試算金額，不是保證的理賠上限；實際以網頁與保險條款為準。"

LIST_QUICK_REPLY_LABEL = "{n} {destination} {depart}"


def _list_item(n: int, item: dict[str, Any]) -> str:
    status = HAS_RECOMMENDATION if item["has_recommendation"] else NO_RECOMMENDATION
    return (
        f"{n}. {item['destination']} "
        f"{item['depart_date']}～{item['return_date']}（{item['days']} 天）{status}"
    )


def _trip_line(trip: dict[str, Any]) -> str:
    return (
        f"{trip['destination']}｜{trip['depart_date']}～{trip['return_date']}"
        f"（{trip['days']} 天）"
    )


def _plan_lines(label: str, plan: dict[str, Any]) -> list[str]:
    return [
        f"{label}：{plan['name']}（{plan['company']}）",
        f"　保額 {plan['sum_insured']:,} 元／保費 {plan['premium']:,} 元",
    ]


def _missing_text(missing: str | None) -> str:
    """工具的 missing 是寫給 AI 看的（「請使用者…」），這裡改成對使用者說的話；對不上就用通用的一句。"""
    from app.mcp_server.tools.get_comparison import (
        MISSING_CONCERNS,
        MISSING_RECOMMENDATION,
        RECOMMENDATION_STALE,
    )

    return {
        MISSING_CONCERNS: MISSING_CONCERNS_TEXT,
        MISSING_RECOMMENDATION: MISSING_RECOMMENDATION_TEXT,
        RECOMMENDATION_STALE: MISSING_STALE_TEXT,
    }.get(missing or "", MISSING_OTHER_TEXT)


def format_list(items: list[dict[str, Any]], *, more: bool) -> str:
    lines = [LIST_HEADER.format(count=len(items))]
    lines += [_list_item(n, item) for n, item in enumerate(items, 1)]
    if more:
        lines.append(LIST_MORE_NOTE.format(limit=LIST_LIMIT))
    lines += ["", LIST_FOOTER]
    return fit_text("\n".join(lines))


def format_detail(detail: dict[str, Any]) -> str:
    lines = [_trip_line(detail["trip"])]
    if detail["concerns"]:
        lines.append("在意事項：" + "、".join(detail["concerns"]))
    if detail["status"] != "complete":
        lines += ["", INCOMPLETE_HEADER, _missing_text(detail.get("missing")), ""]
        lines.append(INCOMPLETE_FOOTER)
        return fit_text("\n".join(lines))
    lines.append("")
    lines += _plan_lines("推薦方案", detail["recommended"])
    lines += _plan_lines("次推薦", detail["alternative"])
    lines += ["", DISCLAIMER]
    return fit_text("\n".join(lines))


def list_quick_replies(items: list[dict[str, Any]]) -> tuple[QuickReply, ...]:
    return clamp_quick_replies(
        [
            QuickReply(
                LIST_QUICK_REPLY_LABEL.format(
                    n=n, destination=item["destination"], depart=item["depart_date"][5:]
                ),
                f"{texts.CMD_COMPARE} {n}",
            )
            for n, item in enumerate(items, 1)
        ]
    )


NAV_QUICK_REPLIES = (
    QuickReply(texts.CMD_COMPARE, texts.CMD_COMPARE),
    QuickReply(texts.CMD_TODO, texts.CMD_TODO),
    QuickReply(texts.CMD_HELP, texts.CMD_HELP),
)


async def _reply_detail_for_postback(ctx: LineContext, raw_id: str) -> None:
    """卡片「看推薦」：以按鈕帶的比較紀錄 id 取那一筆（不看列表順序），回詳情卡。"""
    try:
        comparison_id = uuid.UUID(raw_id)
    except ValueError:
        await ctx.reply(NOT_FOUND_TEXT, NAV_QUICK_REPLIES)
        return
    detail = await call_mcp_tool(
        ctx.session_maker,
        ctx.line_user_id,
        "get_comparison",
        {"comparison_id": str(comparison_id)},
    )
    if isinstance(detail, McpFailure) and _tool_not_found() in detail.message:
        await ctx.reply(NOT_FOUND_TEXT, NAV_QUICK_REPLIES)
        return
    if await reply_if_not_success(ctx, detail):
        return
    assert isinstance(detail, McpSuccess)
    content = detail.content
    text = format_detail(content)
    await ctx.reply_flex(
        text,
        lambda: detail_bubble(
            content,
            _missing_text(content.get("missing"))
            if content["status"] != "complete"
            else None,
        ),
        fallback_quick_replies=NAV_QUICK_REPLIES,  # 卡片有按鈕，降級純文字才補快速回覆
    )


def _tool_not_found() -> str:
    from app.mcp_server.tools.get_comparison import NOT_FOUND

    return NOT_FOUND


@line_command(texts.CMD_COMPARE)
async def compare_command(ctx: LineContext) -> None:
    if raw_id := ctx.postback.get("id"):
        await _reply_detail_for_postback(ctx, raw_id)
        return
    index: int | None = None
    if ctx.args:
        if not ctx.args.isdecimal():
            await ctx.reply(USAGE_TEXT, NAV_QUICK_REPLIES)
            return
        index = int(ctx.args[:18])  # 太長的數字一律算超出（訊息裡仍顯示原樣）
        if len(ctx.args) > 18:
            index = 10**18

    listed = await call_mcp_tool(
        ctx.session_maker,
        ctx.line_user_id,
        "list_comparisons",
        {"limit": LIST_LIMIT},
    )
    if await reply_if_not_success(ctx, listed):
        return
    assert isinstance(listed, McpSuccess)
    items: list[dict[str, Any]] = listed.content.get("comparisons", [])

    if not items:
        await ctx.reply(EMPTY_TEXT, NAV_QUICK_REPLIES[1:])
        return
    if index is None:
        more = len(items) >= LIST_LIMIT
        await ctx.reply_flex(
            format_list(items, more=more),
            lambda: compare_carousel(items, more_limit=LIST_LIMIT if more else None),
            fallback_quick_replies=list_quick_replies(items),
        )
        return
    if not 1 <= index <= len(items):
        if len(items) >= LIST_LIMIT and index > len(items):
            text = OUT_OF_RANGE_BEYOND_TEXT.format(n=ctx.args, limit=LIST_LIMIT)
        else:
            text = OUT_OF_RANGE_TEXT.format(n=ctx.args, count=len(items))
        await ctx.reply(text, list_quick_replies(items))
        return

    detail = await call_mcp_tool(
        ctx.session_maker,
        ctx.line_user_id,
        "get_comparison",
        {"comparison_id": items[index - 1]["id"]},
    )
    if await reply_if_not_success(ctx, detail):
        return
    assert isinstance(detail, McpSuccess)
    await ctx.reply(format_detail(detail.content), NAV_QUICK_REPLIES)
