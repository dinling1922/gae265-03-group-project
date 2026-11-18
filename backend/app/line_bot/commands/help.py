from app.line_bot import texts
from app.line_bot.registry import LineContext, line_command


@line_command(texts.CMD_HELP)
async def help_command(ctx: LineContext) -> None:
    await ctx.reply(texts.HELP_TEXT, texts.HELP_QUICK_REPLIES)
