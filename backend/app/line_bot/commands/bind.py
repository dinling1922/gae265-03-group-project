"""綁定 <6 位數綁定碼>：把這個 LINE 連結到網站帳號（ADR-0033、#487）。

綁定碼是使用者自己傳來的，仍不回顯、不寫 log；權杖明文只在 `domain.line_binding` 裡加密存起來。
"""

import logging
import re
import unicodedata

from app.core.token_cipher import cipher_from_settings
from app.domain.line_binding import (
    BindOutcome,
    LineBindingUnavailable,
    bind_line,
)
from app.line_bot import texts
from app.line_bot.client import QuickReply
from app.line_bot.registry import LineContext, line_command

logger = logging.getLogger(__name__)

_CODE = re.compile(r"\d{6}")

_FAILURE_TEXT = {
    BindOutcome.WRONG: texts.BIND_WRONG_TEXT,
    BindOutcome.EXPIRED: texts.BIND_EXPIRED_TEXT,
    BindOutcome.USED: texts.BIND_USED_TEXT,
    BindOutcome.THROTTLED: texts.BIND_THROTTLED_TEXT,
    BindOutcome.LINE_TAKEN: texts.BIND_LINE_TAKEN_TEXT,
    BindOutcome.ALREADY_BOUND: texts.BIND_ALREADY_TEXT,
    BindOutcome.TOKEN_LIMIT: texts.BIND_TOKEN_LIMIT_TEXT,
}


def normalize_code(args: str) -> str | None:
    """去掉空白、全形數字轉半形（中文輸入法常打出全形）；不是 6 位數回 None。"""
    code = unicodedata.normalize("NFKC", args)
    code = re.sub(r"\s+", "", code)
    return code if _CODE.fullmatch(code) else None


@line_command(texts.CMD_BIND)
async def bind_command(ctx: LineContext) -> None:
    code = normalize_code(ctx.args)
    if code is None or not ctx.line_user_id:
        await ctx.reply(texts.BIND_USAGE_TEXT)
        return
    try:
        async with ctx.session_maker() as session:
            result = await bind_line(
                session, ctx.line_user_id, code, cipher_from_settings()
            )
    except LineBindingUnavailable:
        logger.error("LINE 綁定失敗：伺服器沒有設定 TOKEN_ENCRYPTION_KEY")
        await ctx.reply(texts.BIND_ERROR_TEXT)
        return
    if result.outcome == BindOutcome.SUCCESS:
        await ctx.reply(
            texts.BIND_SUCCESS_REPLACED_TEXT
            if result.replaced
            else texts.BIND_SUCCESS_TEXT,
            [
                QuickReply(texts.CMD_COMPARE, texts.CMD_COMPARE),
                QuickReply(texts.CMD_TODO, texts.CMD_TODO),
            ],
        )
        return
    await ctx.reply(_FAILURE_TEXT[result.outcome])
