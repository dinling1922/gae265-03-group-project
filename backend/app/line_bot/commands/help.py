from app.line_bot import texts
from app.line_bot.cards.menu import reply_menu
from app.line_bot.registry import LineContext, line_command


@line_command(texts.CMD_HELP)
async def help_command(ctx: LineContext) -> None:
    await reply_menu(ctx)
