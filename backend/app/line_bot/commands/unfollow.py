"""使用者封鎖或刪除官方帳號（`unfollow`）：解除綁定並撤銷權杖（ADR-0033、#487）。

unfollow 事件沒有回覆權杖，不回任何訊息。
"""

from app.domain.line_binding import unbind_line
from app.line_bot.registry import LineContext, line_event


@line_event("unfollow")
async def unfollow(ctx: LineContext) -> None:
    if not ctx.line_user_id:
        return
    async with ctx.session_maker() as session:
        await unbind_line(session, ctx.line_user_id)
