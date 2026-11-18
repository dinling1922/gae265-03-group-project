"""暫時的佔位：指令還沒實作（LINE-2 會改寫這個檔案）。"""

from app.line_bot import texts
from app.line_bot.registry import LineContext, line_command


@line_command(texts.CMD_BIND)
async def bind_command(ctx: LineContext) -> None:
    await ctx.reply(texts.NOT_READY_TEXT)
