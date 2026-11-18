"""LINE 綁定（ADR-0033、#487）：綁定碼、綁定、解除，以及綁定碼的防暴力猜測。

威脅模型（綁定碼只有 6 位數，100 萬種）：
- 攻擊者加 bot 好友、亂傳「綁定 NNNNNN」，想猜中別人剛產生的碼，把受害者的資料綁到自己的 LINE。
  碼 10 分鐘有效、同時有效的碼很少（K 組），單次猜中機率約 K / 100 萬，所以重點是限制猜的次數：
  1. 同一個 LINE userId 在視窗內最多傳錯 `LINE_BIND_MAX_FAILURES`（預設 5）次，超過暫時擋下（連正確的碼也不處理）。
  2. 但 LINE 帳號可以多開，只限單一 userId 擋不住輪流猜，所以另有全部 LINE 帳號合計的上限
     `LINE_BIND_GLOBAL_MAX_FAILURES`（預設 100／視窗）。取捨：攻擊者可以燒光合計額度讓正常使用者暫時不能綁定
     （阻斷服務，視窗過後恢復），但不能因此綁到別人的帳號；以資料保護優先。
  3. 碼只存 HMAC-SHA256（金鑰是 TOKEN_ENCRYPTION_KEY），資料庫外洩看不到碼也不能離線窮舉。
- 傳錯、過期、已用過都算一次錯誤（它們代表「猜到了存在的碼」，不該比猜錯便宜）；格式不對（不是 6 位數）沒有猜測，不計。
- 對方持有正確的碼但 LINE 已綁別人、或使用者已綁同一個 LINE：不算錯誤，也不消耗碼。
- 計數與判斷用 PostgreSQL 的 advisory lock 依 LINE userId 序列化，同一個帳號並行傳也不能多猜。
"""

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.token_cipher import TokenCipher
from app.domain.access_tokens import SCOPE_READ, TokenLimitError, create_token
from app.models import ApiToken, LineBindCode, LineBindFailure, LineBinding

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)
CODE_DIGITS = 6
LINE_TOKEN_NAME = "LINE 綁定"
# LINE 的權杖永遠唯讀（ADR-0034）；綁定與自動換新都用這個，不接受呼叫端指定
LINE_TOKEN_SCOPES = (SCOPE_READ,)
_MAX_ISSUE_ATTEMPTS = 10


class LineBindingUnavailable(Exception):
    """伺服器缺少 TOKEN_ENCRYPTION_KEY，綁定碼雜湊與權杖加密都做不了。"""


def _hmac_key() -> bytes:
    if settings.TOKEN_ENCRYPTION_KEY is None:
        raise LineBindingUnavailable
    return settings.TOKEN_ENCRYPTION_KEY.get_secret_value().encode()


def hash_code(code: str) -> str:
    return hmac.new(_hmac_key(), code.encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


# ---- 綁定碼 ----


async def issue_code(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, datetime]:
    """產生新的綁定碼（回明文與到期時間）；覆蓋同一人的舊碼，所以舊碼立即失效，也包含已用過的。"""
    expires_at = _now() + CODE_TTL
    for _ in range(_MAX_ISSUE_ATTEMPTS):
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        stmt = pg_insert(LineBindCode).values(
            id=uuid.uuid4(),
            user_id=user_id,
            code_hash=hash_code(code),
            expires_at=expires_at,
            used_at=None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "code_hash": stmt.excluded.code_hash,
                "expires_at": expires_at,
                "used_at": None,
                "updated_at": _now(),
            },
        )
        try:
            await session.exec(stmt)
            await session.commit()
        except IntegrityError:  # 碰巧和別人手上的碼（含過期、用過）撞號：換一組
            await session.rollback()
            continue
        return code, expires_at
    raise RuntimeError("產生綁定碼連續撞號")  # 100 萬組裡撞 10 次，實際上不會發生


@dataclass(frozen=True)
class LineBindingStatus:
    bound: bool
    bound_at: datetime | None
    code_expires_at: datetime | None  # 有未過期、未使用的碼才有值；不含碼明文


async def get_status(session: AsyncSession, user_id: uuid.UUID) -> LineBindingStatus:
    binding = (
        await session.exec(select(LineBinding).where(LineBinding.user_id == user_id))
    ).first()
    code = (
        await session.exec(select(LineBindCode).where(LineBindCode.user_id == user_id))
    ).first()
    live = code is not None and code.used_at is None and code.expires_at > _now()
    return LineBindingStatus(
        bound=binding is not None,
        bound_at=binding.bound_at if binding else None,
        code_expires_at=code.expires_at if live and code else None,
    )


# ---- 解除 ----


async def revoke_token_row(session: AsyncSession, token_id: uuid.UUID) -> None:
    """撤銷權杖（不 commit）；已撤銷的不改第一次的時間。"""
    await session.exec(
        update(ApiToken)
        .where(col(ApiToken.id) == token_id, col(ApiToken.revoked_at).is_(None))
        .values(revoked_at=_now())
    )


async def remove_binding(session: AsyncSession, binding: LineBinding) -> None:
    """刪綁定並撤銷那把權杖，一個交易（不 commit，呼叫端決定）。"""
    await revoke_token_row(session, binding.api_token_id)
    await session.delete(binding)
    await session.flush()


async def unbind_user(session: AsyncSession, user_id: uuid.UUID) -> bool:
    binding = (
        await session.exec(select(LineBinding).where(LineBinding.user_id == user_id))
    ).first()
    if binding is None:
        return False
    await remove_binding(session, binding)
    await session.commit()
    return True


async def unbind_line(session: AsyncSession, line_user_id: str) -> bool:
    binding = (
        await session.exec(
            select(LineBinding).where(LineBinding.line_user_id == line_user_id)
        )
    ).first()
    if binding is None:
        return False
    await remove_binding(session, binding)
    await session.commit()
    return True


# ---- 綁定 ----


class BindOutcome(StrEnum):
    SUCCESS = "success"
    THROTTLED = "throttled"  # 錯誤次數超過限制，暫時擋下
    WRONG = "wrong"
    EXPIRED = "expired"
    USED = "used"
    LINE_TAKEN = "line_taken"  # 這個 LINE 已綁定其他帳號
    ALREADY_BOUND = "already_bound"  # 這個 LINE 已綁定這個帳號
    TOKEN_LIMIT = "token_limit"  # 帳號的有效存取權杖已滿 5 把，建不了綁定用的權杖


@dataclass(frozen=True)
class BindResult:
    outcome: BindOutcome
    replaced: bool = False  # 成功時，是否取代了這個帳號原本綁的另一個 LINE


async def _is_throttled(session: AsyncSession, line_user_id: str) -> bool:
    since = _now() - timedelta(minutes=settings.LINE_BIND_FAILURE_WINDOW_MINUTES)
    mine = (
        await session.exec(
            select(func.count())
            .select_from(LineBindFailure)
            .where(
                col(LineBindFailure.line_user_id) == line_user_id,
                col(LineBindFailure.created_at) > since,
            )
        )
    ).one()
    if mine >= settings.LINE_BIND_MAX_FAILURES:
        return True
    total = (
        await session.exec(
            select(func.count())
            .select_from(LineBindFailure)
            .where(col(LineBindFailure.created_at) > since)
        )
    ).one()
    return total >= settings.LINE_BIND_GLOBAL_MAX_FAILURES


async def _record_failure(session: AsyncSession, line_user_id: str) -> None:
    since = _now() - timedelta(minutes=settings.LINE_BIND_FAILURE_WINDOW_MINUTES)
    await session.exec(
        delete(LineBindFailure).where(col(LineBindFailure.created_at) <= since)
    )  # 順手清掉視窗外的
    session.add(LineBindFailure(line_user_id=line_user_id))
    await session.commit()


async def bind_line(
    session: AsyncSession, line_user_id: str, code: str, cipher: TokenCipher
) -> BindResult:
    """用綁定碼把 line_user_id 綁到碼的主人。`code` 是已正規化的 6 位數字串。

    成功時：建一把名稱「LINE 綁定」的唯讀權杖，明文加密存進 line_binding，並把碼標成已使用。
    綁定碼的主人已經綁了另一個 LINE：視為使用者要換 LINE（碼只有登入的本人能產生），舊綁定與舊權杖一併解除（`replaced=True`）。
    """
    # 同一個 LINE userId 的判斷與計數序列化（交易層級，commit 後釋放）
    await session.exec(
        select(func.pg_advisory_xact_lock(func.hashtext(f"line_bind:{line_user_id}")))
    )
    if await _is_throttled(session, line_user_id):
        await session.rollback()
        return BindResult(BindOutcome.THROTTLED)

    row = (
        await session.exec(
            select(LineBindCode).where(LineBindCode.code_hash == hash_code(code))
        )
    ).first()
    if row is None:
        await _record_failure(session, line_user_id)
        return BindResult(BindOutcome.WRONG)
    if row.used_at is not None:
        await _record_failure(session, line_user_id)
        return BindResult(BindOutcome.USED)
    if row.expires_at <= _now():
        await _record_failure(session, line_user_id)
        return BindResult(BindOutcome.EXPIRED)

    owner_id = row.user_id
    row_id = row.id  # create_token 失敗會 rollback，之後不能再碰 row 的屬性
    same_line = (
        await session.exec(
            select(LineBinding).where(LineBinding.line_user_id == line_user_id)
        )
    ).first()
    if same_line is not None:
        outcome = (
            BindOutcome.ALREADY_BOUND
            if same_line.user_id == owner_id
            else BindOutcome.LINE_TAKEN
        )
        await session.rollback()  # rollback 會讓物件過期，先取好結果
        return BindResult(outcome)
    old = (
        await session.exec(select(LineBinding).where(LineBinding.user_id == owner_id))
    ).first()

    # 先建新權杖再動舊綁定：失敗時使用者還保有原本可用的綁定。
    # 只有「有效權杖滿 5 把、而其中一把正是舊綁定的」才先解除舊的騰出名額
    try:
        token, plaintext = await create_token(
            session, owner_id, LINE_TOKEN_NAME, scopes=LINE_TOKEN_SCOPES
        )
    except TokenLimitError:
        # create_token 已 rollback，重新讀舊綁定
        old = (
            await session.exec(
                select(LineBinding).where(LineBinding.user_id == owner_id)
            )
        ).first()
        if old is None:
            return BindResult(BindOutcome.TOKEN_LIMIT)
        await remove_binding(session, old)
        await session.commit()
        old = None
        try:
            token, plaintext = await create_token(
                session, owner_id, LINE_TOKEN_NAME, scopes=LINE_TOKEN_SCOPES
            )
        except TokenLimitError:
            return BindResult(BindOutcome.TOKEN_LIMIT)
        replaced = True
    else:
        replaced = old is not None

    token_id = token.id
    try:
        consumed = (
            await session.exec(
                update(LineBindCode)
                .where(
                    col(LineBindCode.id) == row_id, col(LineBindCode.used_at).is_(None)
                )
                .values(used_at=_now())
                .returning(col(LineBindCode.id))
            )
        ).first()
        if consumed is None:  # 並行的另一個請求先用掉了
            await revoke_token_row(session, token_id)
            await session.commit()
            return BindResult(BindOutcome.USED)
        if old is not None:
            await remove_binding(session, old)
        session.add(
            LineBinding(
                user_id=owner_id,
                line_user_id=line_user_id,
                token_encrypted=cipher.encrypt(plaintext),
                key_version=cipher.key_version,
                api_token_id=token_id,
                bound_at=_now(),
            )
        )
        await session.exec(
            delete(LineBindFailure).where(
                col(LineBindFailure.line_user_id) == line_user_id
            )
        )
        await session.commit()
    except IntegrityError:  # 並行的另一個請求搶先綁了同一個 LINE 或帳號
        await session.rollback()
        await revoke_token_row(session, token_id)
        await session.commit()
        return BindResult(BindOutcome.LINE_TAKEN)
    return BindResult(BindOutcome.SUCCESS, replaced=replaced)
