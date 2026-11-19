"""「待辦」指令（ADR-0033、#490）：看理賠待辦，依行程→事件分組。

- `待辦`：呼叫 `list_todos`（預設 pending），回未完成的待辦
- `待辦 已完成`（也接受 `待辦 done`）：回已完成的
- 其他參數：回用法說明

每個行程附完成數／總數（`list_todos` 給的 done／total，與網頁行程頁一致，不受篩選影響）；
共同文件獨立一組；每項標示階段（事故當下／回國後整理）。資料與數字全部原樣取自工具輸出，轉接層不重算、
不增加欄位（ADR-0004、ADR-0032）。沒有範本的待辦，標題沿用工具給的固定字樣。

「沒有行程」與「全部完成」分開說：行程是指已確認事故的保單，沒有行程代表網頁上還沒有待辦；
有行程但沒有符合篩選的項目時，仍列出每個行程的完成數／總數。
文案都是暫定，要請設計者確認。
"""

from typing import Any

from app.line_bot import texts
from app.line_bot.client import QuickReply
from app.line_bot.failures import reply_if_not_success
from app.line_bot.limits import clamp_quick_replies, fit_text
from app.line_bot.mcp_client import McpSuccess, call_mcp_tool
from app.line_bot.registry import LineContext, line_command

# 接受的參數（比對時轉小寫、去空白）
PENDING_ARGS = {""}
DONE_ARGS = {"已完成", "done"}

# ---- 暫定文案（待設計者確認） ----
USAGE_TEXT = "請傳「待辦」看未完成的理賠待辦，或傳「待辦 已完成」看已完成的。"
NO_TRIPS_TEXT = (
    "目前沒有行程，所以還沒有理賠待辦。\n"
    "在網頁上傳保單並確認事故之後，待辦就會出現在這裡。"
)
PENDING_TITLE = "未完成的理賠待辦"
DONE_TITLE = "已完成的理賠待辦"
PENDING_NONE_TEXT = "沒有未完成的待辦，全部完成了。"
PENDING_NONE_PARTIAL_TEXT = "目前沒有未完成的待辦可以列出。"
DONE_NONE_TEXT = "還沒有已完成的待辦。"
TRIP_NONE_TEXT = "　（這個行程沒有符合的待辦）"
SHARED_TITLE = "共同文件（整個行程只收一份）"
PHASE_LABELS = {"on_site": "事故當下", "after_return": "回國後整理"}
UNKNOWN_DESTINATION = "行程"
UNKNOWN_INCIDENT = "事故（類型未定）"
FOOTER = "要勾選或補文件，請回網頁處理。"

QUICK_REPLIES_PENDING = (
    QuickReply("已完成待辦", f"{texts.CMD_TODO} 已完成"),
    QuickReply(texts.CMD_COMPARE, texts.CMD_COMPARE),
    QuickReply(texts.CMD_HELP, texts.CMD_HELP),
)
QUICK_REPLIES_DONE = (
    QuickReply("未完成待辦", texts.CMD_TODO),
    QuickReply(texts.CMD_COMPARE, texts.CMD_COMPARE),
    QuickReply(texts.CMD_HELP, texts.CMD_HELP),
)


def _todo_line(todo: dict[str, Any]) -> str:
    phase = PHASE_LABELS.get(todo["phase"], todo["phase"])
    return f"　‧ {todo['title']}（{phase}）"


def _trip_header(trip: dict[str, Any]) -> str:
    where = trip["destination"] or UNKNOWN_DESTINATION
    if trip["start_date"] and trip["end_date"]:
        where += f" {trip['start_date']}～{trip['end_date']}"
    return f"【{where}】已完成 {trip['done']}／{trip['total']}"


def _incident_header(group: dict[str, Any]) -> str:
    name = group["incident_type"] or UNKNOWN_INCIDENT
    if group["occurred_date"]:
        name += f"（{group['occurred_date']}）"
    return f"▍{name}"


def format_todos(content: dict[str, Any]) -> str:
    done_view = content["status"] == "done"
    trips: list[dict[str, Any]] = content["trips"]
    lines = [DONE_TITLE if done_view else PENDING_TITLE]
    any_listed = False
    for trip in trips:
        lines += ["", _trip_header(trip)]
        shown = False
        for group in trip["incidents"]:
            lines.append(_incident_header(group))
            lines += [_todo_line(t) for t in group["todos"]]
            shown = shown or bool(group["todos"])
        if trip["shared_documents"]:
            lines.append(f"▍{SHARED_TITLE}")
            lines += [_todo_line(t) for t in trip["shared_documents"]]
            shown = True
        if not shown:
            lines.append(TRIP_NONE_TEXT)
        any_listed = any_listed or shown
    if not any_listed:
        if done_view:
            lines.insert(1, DONE_NONE_TEXT)
        else:
            # 全部完成：每個行程 done == total；否則是工具沒列出的情況（例如待辦都不適用）
            all_done = all(t["done"] == t["total"] for t in trips) and any(
                t["total"] > 0 for t in trips
            )
            lines.insert(
                1, PENDING_NONE_TEXT if all_done else PENDING_NONE_PARTIAL_TEXT
            )
    lines += ["", FOOTER]
    return fit_text("\n".join(lines))


@line_command(texts.CMD_TODO)
async def todo_command(ctx: LineContext) -> None:
    arg = ctx.args.strip().lower()
    if arg in PENDING_ARGS:
        status = "pending"
    elif arg in DONE_ARGS:
        status = "done"
    else:
        await ctx.reply(USAGE_TEXT, clamp_quick_replies(QUICK_REPLIES_PENDING))
        return
    result = await call_mcp_tool(
        ctx.session_maker, ctx.line_user_id, "list_todos", {"status": status}
    )
    if await reply_if_not_success(ctx, result):
        return
    assert isinstance(result, McpSuccess)
    quick = QUICK_REPLIES_DONE if status == "done" else QUICK_REPLIES_PENDING
    if not result.content.get("trips"):
        await ctx.reply(NO_TRIPS_TEXT, quick)
        return
    from app.line_bot.cards.todo import (
        todo_card,  # 卡片要用本檔的文案常數，延後匯入避開循環
    )

    content = result.content
    await ctx.reply_flex(
        format_todos(content),
        lambda: todo_card(content),
        quick_replies=quick,
    )
