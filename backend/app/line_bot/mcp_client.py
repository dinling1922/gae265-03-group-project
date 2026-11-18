"""轉接層呼叫 MCP 伺服器的共用函式（ADR-0033、#487）：#489 比較、#490 待辦的指令直接用它。

    result = await call_mcp_tool(ctx.session_maker, ctx.line_user_id, "list_comparisons", {"limit": 5})
    match result:
        case McpSuccess(content=data): ...   # data 是工具的結構化內容（dict），數字與文字都照用，不重算
        case McpNotBound(): await ctx.reply(texts.NOT_BOUND_TEXT)
        case McpBindingInvalid(): await ctx.reply(texts.BINDING_INVALID_TEXT)
        case McpRateLimited(): await ctx.reply(texts.RATE_LIMITED_TEXT)
        case McpFailure(): await ctx.reply(texts.MCP_ERROR_TEXT)

做法：取出該 LINE 的綁定、解密權杖，經行程內 ASGI 傳輸（不走網路、不需要對外網址）以 JSON-RPC 的 `tools/call` 呼叫
`/mcp` 的真實守門層，所以 Bearer 驗證、每把權杖限流、稽核紀錄都照樣經過；轉接層不直接讀任何資料表。

權杖自動換新：權杖剩不到 14 天時，呼叫前先換一把新的。順序是「先建新、再原子地切換綁定、最後撤銷舊的」：
- 建新權杖失敗（含有效權杖已滿 5 把而建不了）：照樣用還有效的舊權杖，下次呼叫再試；不會讓使用者失去目前可用的綁定
- 切換綁定用 `WHERE api_token_id = 舊的` 的條件更新：並行的另一個呼叫已先換掉就放棄、撤銷自己剛建的、改讀新的綁定
- 切換成功後才撤銷舊權杖；撤銷失敗只記 log（舊的本來就快到期，明文也已不存在於資料庫）
- 兩步之間行程中斷，最壞是多一把閒置的「LINE 綁定」權杖，使用者在帳號頁看得到、可撤銷
"""

import asyncio
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, select
from starlette.types import ASGIApp

from app.core.token_cipher import TokenCipher, TokenDecryptError, cipher_from_settings
from app.domain.access_tokens import TokenLimitError, create_token, token_status
from app.domain.line_binding import LINE_TOKEN_NAME, remove_binding, revoke_token_row
from app.line_bot.registry import SessionMaker
from app.models import ApiToken, LineBinding

logger = logging.getLogger(__name__)

ROTATE_WITHIN = timedelta(days=14)
CALL_TIMEOUT_SECONDS = 20.0
_MCP_URL = "http://mcp.internal/mcp"  # 行程內傳輸，主機名稱只是形式（守門層不看 Host）


@dataclass(frozen=True)
class McpSuccess:
    """成功：`content` 是工具的結構化內容（MCP 的 structuredContent）。"""

    content: dict[str, Any]


@dataclass(frozen=True)
class McpNotBound:
    """這個 LINE 沒有綁定。"""


@dataclass(frozen=True)
class McpBindingInvalid:
    """綁定已失效（權杖被撤銷、過期，或解不開）。綁定已一併清除；呼叫端回「綁定已失效，請重新綁定」。"""


@dataclass(frozen=True)
class McpRateLimited:
    """被每把權杖的限流擋下（429）。"""

    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class McpFailure:
    """其他錯誤（工具回錯、逾時、連線例外）。`message` 給 log 看，不要原樣回給 LINE 使用者。"""

    message: str


McpResult = McpSuccess | McpNotBound | McpBindingInvalid | McpRateLimited | McpFailure


@dataclass(frozen=True)
class _Credentials:
    binding_id: uuid.UUID
    user_id: uuid.UUID
    token_id: uuid.UUID
    plaintext: str
    expires_at: datetime


class _Invalid:
    pass


async def _drop_binding(session_maker: SessionMaker, line_user_id: str) -> None:
    """清掉失效的綁定（及權杖，若還沒撤銷）。"""
    async with session_maker() as session:
        binding = (
            await session.exec(
                select(LineBinding).where(LineBinding.line_user_id == line_user_id)
            )
        ).first()
        if binding is not None:
            await remove_binding(session, binding)
            await session.commit()


async def _load(
    session_maker: SessionMaker, line_user_id: str, cipher: TokenCipher
) -> _Credentials | McpNotBound | _Invalid:
    async with session_maker() as session:
        binding = (
            await session.exec(
                select(LineBinding).where(LineBinding.line_user_id == line_user_id)
            )
        ).first()
        if binding is None:
            return McpNotBound()
        token = await session.get(ApiToken, binding.api_token_id)
        if token is None or token_status(token) != "active":
            return _Invalid()
        try:
            plaintext = cipher.decrypt(binding.token_encrypted)
        except TokenDecryptError:
            return _Invalid()
        return _Credentials(
            binding_id=binding.id,
            user_id=binding.user_id,
            token_id=token.id,
            plaintext=plaintext,
            expires_at=token.expires_at,
        )


async def _rotate(
    session_maker: SessionMaker, cred: _Credentials, cipher: TokenCipher
) -> _Credentials | None:
    """換新權杖；回新的憑證，或 None 表示沒換成（呼叫端沿用舊的或重新讀取）。"""
    async with session_maker() as session:
        try:
            new_token, new_plain = await create_token(
                session, cred.user_id, LINE_TOKEN_NAME
            )
        except TokenLimitError:
            logger.warning("LINE 綁定權杖換新略過：有效存取權杖已滿上限")
            return None
        new_id, new_expires = new_token.id, new_token.expires_at
        switched = (
            await session.exec(
                update(LineBinding)
                .where(
                    col(LineBinding.id) == cred.binding_id,
                    col(LineBinding.api_token_id) == cred.token_id,
                )
                .values(
                    token_encrypted=cipher.encrypt(new_plain),
                    key_version=cipher.key_version,
                    api_token_id=new_id,
                )
                .returning(col(LineBinding.id))
            )
        ).first()
        if switched is None:  # 綁定被別人換過或解除了：放棄，撤銷自己建的
            await revoke_token_row(session, new_id)
            await session.commit()
            return None
        await session.commit()
        try:
            await revoke_token_row(session, cred.token_id)
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            logger.exception("LINE 綁定的舊權杖撤銷失敗（新的已生效）")
    return _Credentials(cred.binding_id, cred.user_id, new_id, new_plain, new_expires)


def _default_asgi() -> ASGIApp:
    from app.main import mcp_endpoint  # 延遲載入：app.main 會匯入 line_bot

    return mcp_endpoint.asgi


async def call_mcp_tool(
    session_maker: SessionMaker,
    line_user_id: str,
    tool: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    asgi: ASGIApp | None = None,
    cipher: TokenCipher | None = None,
) -> McpResult:
    """以 `line_user_id` 綁定的使用者身分呼叫 MCP 工具 `tool`。永遠回結果物件，不丟例外（呼叫端 `match` 即可）。

    `asgi`、`cipher` 給測試換掉；預設用 app 的 `/mcp` 守門層與 `TOKEN_ENCRYPTION_KEY`。
    """
    cipher = cipher or cipher_from_settings()
    loaded = await _load(session_maker, line_user_id, cipher)
    if isinstance(loaded, McpNotBound):
        return loaded
    if isinstance(loaded, _Invalid):
        await _drop_binding(session_maker, line_user_id)
        return McpBindingInvalid()
    cred = loaded

    if cred.expires_at - datetime.now(UTC) < ROTATE_WITHIN:
        try:
            rotated = await _rotate(session_maker, cred, cipher)
        except Exception:  # 換新失敗不影響這次呼叫：舊權杖還有效，下次再試
            logger.exception("LINE 綁定權杖換新失敗，沿用舊權杖")
            rotated = None
        if rotated is not None:
            cred = rotated
        else:  # 可能是並行的呼叫已換過：重新讀取，讀不到就沿用舊的
            reloaded = await _load(session_maker, line_user_id, cipher)
            if isinstance(reloaded, _Credentials):
                cred = reloaded

    # 守門層是 stateless：不必先 initialize，單一個 JSON-RPC 的 tools/call 請求就夠。
    # 不用 SDK 的客戶端是因為它一次呼叫要送 initialize、initialized、tools/call 三個請求，
    # 每個都會吃掉每分鐘 60 次的限流額度，等於一次指令用掉三次
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": dict(arguments or {})},
    }
    try:
        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=asgi or _default_asgi()),
                headers={"Authorization": f"Bearer {cred.plaintext}"},
            ) as http:
                response = await http.post(
                    _MCP_URL,
                    json=payload,
                    headers={"Accept": "application/json, text/event-stream"},
                )
    except Exception as exc:
        # 不記例外內容：它可能帶出請求標頭（權杖）
        logger.error(
            "LINE 轉接層呼叫 MCP 失敗（tool=%s）：%s", tool, type(exc).__name__
        )
        return McpFailure(type(exc).__name__)
    if response.status_code == 401:
        await _drop_binding(session_maker, line_user_id)
        return McpBindingInvalid()
    if response.status_code == 429:
        retry_after = response.headers.get("retry-after", "")
        return McpRateLimited(int(retry_after) if retry_after.isdigit() else None)
    if response.status_code != 200:
        logger.error(
            "LINE 轉接層呼叫 MCP 失敗（tool=%s，http=%s）", tool, response.status_code
        )
        return McpFailure(f"http {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return McpFailure("回應不是 JSON")
    if "error" in body:
        return McpFailure(str(body["error"].get("message", "rpc error")))
    result = body.get("result") or {}
    if result.get("isError"):
        text = " ".join(
            str(block.get("text", "")) for block in result.get("content", [])
        ).strip()
        return McpFailure(text or "tool error")
    return McpSuccess(dict(result.get("structuredContent") or {}))
