"""指令呼叫 MCP 沒成功時的共用回覆（比較、待辦用）：未綁定、綁定失效、限流、其他錯誤各回對應文案。"""

from app.line_bot import texts
from app.line_bot.mcp_client import (
    McpBindingInvalid,
    McpFailure,
    McpNotBound,
    McpRateLimited,
    McpResult,
    McpSuccess,
)
from app.line_bot.registry import LineContext


async def reply_if_not_success(ctx: LineContext, result: McpResult) -> bool:
    """McpSuccess 以外的結果（未綁定、失效、限流、錯誤）統一在這裡回覆；回 True 表示已回覆。比較、待辦共用。"""
    match result:
        case McpSuccess():
            return False
        case McpNotBound():
            await ctx.reply(texts.NOT_BOUND_TEXT)
        case McpBindingInvalid():
            await ctx.reply(texts.BINDING_INVALID_TEXT)
        case McpRateLimited():
            await ctx.reply(texts.RATE_LIMITED_TEXT)
        case McpFailure():
            await ctx.reply(texts.MCP_ERROR_TEXT)
    return True
