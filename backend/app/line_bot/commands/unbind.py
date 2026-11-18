"""解除綁定：刪除綁定並撤銷那把存取權杖（ADR-0033、#487）。"""

from app.domain.line_binding import unbind_line
from app.line_bot import texts
from app.line_bot.registry import LineContext, line_command


@line_command(texts.CMD_UNBIND)
async def unbind_command(ctx: LineContext) -> None:
    async with ctx.session_maker() as session:
        removed = await unbind_line(session, ctx.line_user_id)
    await ctx.reply(
        texts.UNBIND_SUCCESS_TEXT if removed else texts.UNBIND_NOT_BOUND_TEXT
    )
