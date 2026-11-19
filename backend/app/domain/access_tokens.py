"""存取權杖（ADR-0032、#471）：建立、列出、撤銷，以及 MCP 層用的驗證函式。

MCP 層與帳號頁 API 共用這個身分介面；MCP 層只需要 `authenticate_bearer` 與 `TokenIdentity`。
撤銷與到期每次驗證都查資料庫，不快取。
"""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import ApiToken, User

TOKEN_PREFIX = "hyi_"  # 好險有你；讓外洩的字串容易被辨識與掃描
DISPLAY_PREFIX_LEN = len(TOKEN_PREFIX) + 4
DEFAULT_TTL = timedelta(days=90)
MAX_ACTIVE_TOKENS = 5
SCOPE_READ = "read"
SCOPE_WRITE = "write"
# 允許的組合：write 一定附帶 read（只有 write 的權杖連守門層都過不了），預設唯讀（ADR-0034）
ALLOWED_SCOPE_SETS: tuple[tuple[str, ...], ...] = (
    (SCOPE_READ,),
    (SCOPE_READ, SCOPE_WRITE),
)

TokenStatus = Literal["active", "expired", "revoked"]


class TokenLimitError(Exception):
    """有效權杖已達上限。"""


class InvalidScopesError(ValueError):
    """權限範圍不是允許的組合（只有 read，或 read 加 write）。"""


@dataclass(frozen=True)
class TokenIdentity:
    """驗證成功的結果：權杖代表的使用者與權限範圍。"""

    user: User
    token_id: uuid.UUID
    scopes: frozenset[str]


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def token_status(token: ApiToken, now: datetime | None = None) -> TokenStatus:
    now = now or datetime.now(UTC)
    if token.revoked_at is not None:
        return "revoked"
    if token.expires_at <= now:
        return "expired"
    return "active"


async def create_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    scopes: Sequence[str] = (SCOPE_READ,),
) -> tuple[ApiToken, str]:
    """建立權杖，回傳（資料列, 明文）；明文之後再也取不回來。有效權杖滿 5 把丟 TokenLimitError。

    `scopes` 預設唯讀；只接受 read 或 read 加 write（順序與重複不拘），其他丟 InvalidScopesError。
    LINE 綁定與換新一律用預設，不傳 write（ADR-0034）。
    """
    normalized = tuple(s for s in (SCOPE_READ, SCOPE_WRITE) if s in set(scopes))
    if normalized not in ALLOWED_SCOPE_SETS or set(scopes) - set(normalized):
        raise InvalidScopesError(f"不支援的權限範圍：{list(scopes)}")
    # 鎖住使用者列，同一人同時建立也不會超過上限
    await session.exec(select(User.id).where(User.id == user_id).with_for_update())
    now = datetime.now(UTC)
    active = (
        await session.exec(
            select(func.count())
            .select_from(ApiToken)
            .where(
                col(ApiToken.user_id) == user_id,
                col(ApiToken.revoked_at).is_(None),
                col(ApiToken.expires_at) > now,
            )
        )
    ).one()
    if active >= MAX_ACTIVE_TOKENS:
        await session.rollback()
        raise TokenLimitError
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        user_id=user_id,
        name=name,
        token_hash=hash_token(plaintext),
        prefix=plaintext[:DISPLAY_PREFIX_LEN],
        scopes=list(normalized),
        expires_at=now + DEFAULT_TTL,
    )
    session.add(token)
    await session.commit()
    return token, plaintext


async def list_tokens(session: AsyncSession, user_id: uuid.UUID) -> list[ApiToken]:
    """這位使用者的所有權杖，最新在前。"""
    return list(
        (
            await session.exec(
                select(ApiToken)
                .where(ApiToken.user_id == user_id)
                .order_by(col(ApiToken.created_at).desc())
            )
        ).all()
    )


async def revoke_token(
    session: AsyncSession, user_id: uuid.UUID, token_id: uuid.UUID
) -> bool:
    """撤銷自己的權杖；不存在或不是自己的回 False。重複撤銷不改第一次的時間。"""
    token = (
        await session.exec(
            select(ApiToken).where(
                ApiToken.id == token_id, col(ApiToken.user_id) == user_id
            )
        )
    ).first()
    if token is None:
        return False
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        session.add(token)
        await session.commit()
    return True


async def authenticate_bearer(
    session: AsyncSession, bearer: str
) -> TokenIdentity | None:
    """驗證 Bearer 字串（不含 "Bearer " 前綴）。

    有效就更新最後使用時間並回身分；不認得、已撤銷、已過期一律回 None（不洩漏是哪一種）。
    """
    plaintext = bearer.strip()
    if not plaintext.startswith(TOKEN_PREFIX):
        return None
    digest = hash_token(plaintext)
    token = (
        await session.exec(select(ApiToken).where(ApiToken.token_hash == digest))
    ).first()
    if token is None or not hmac.compare_digest(token.token_hash, digest):
        return None
    now = datetime.now(UTC)
    if token_status(token, now) != "active":
        return None
    user = await session.get(User, token.user_id)
    if user is None:
        return None
    token.last_used_at = now
    session.add(token)
    await session.commit()
    return TokenIdentity(user=user, token_id=token.id, scopes=frozenset(token.scopes))
