"""LINE 綁定管理（ADR-0033、#487）：產生綁定碼、查綁定狀態、解除；用 cookie session 認證。

LINE 沒設定（`settings.line_enabled` 為 False）時三個端點都回 404：這功能整個關閉，帳號頁不顯示入口。
綁定碼明文只在產生時回傳這一次；狀態只說「有沒有未過期的碼」與到期時間。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.domain.line_binding import (
    CODE_TTL,
    LineBindingUnavailable,
    get_status,
    issue_code,
    unbind_user,
)

router = APIRouter(prefix="/me/line-binding", tags=["line-binding"])


def _require_enabled() -> None:
    if not settings.line_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "沒有啟用 LINE 功能")


class LineBindingStatusOut(BaseModel):
    bound: bool = Field(description="是否已綁定 LINE")
    bound_at: datetime | None = Field(description="綁定時間；未綁定時是 null")
    code_expires_at: datetime | None = Field(
        description="有未過期、未使用的綁定碼時的到期時間；沒有時是 null。不含碼明文"
    )


class LineBindCodeOut(BaseModel):
    code: str = Field(description="6 位數綁定碼；只在產生時回傳這一次")
    expires_at: datetime
    ttl_seconds: int = Field(description="有效秒數")


@router.get("")
async def get_line_binding(
    session: SessionDep, user: CurrentUser
) -> LineBindingStatusOut:
    """目前的 LINE 綁定狀態；LINE 沒啟用回 404。"""
    _require_enabled()
    s = await get_status(session, user.id)
    return LineBindingStatusOut(
        bound=s.bound, bound_at=s.bound_at, code_expires_at=s.code_expires_at
    )


@router.post("/code", status_code=status.HTTP_201_CREATED)
async def create_line_bind_code(
    session: SessionDep, user: CurrentUser
) -> LineBindCodeOut:
    """產生綁定碼（10 分鐘內有效、只能用一次）；重新產生會讓舊的失效。已綁定時也能產生，用來換成另一個 LINE。"""
    _require_enabled()
    try:
        code, expires_at = await issue_code(session, user.id)
    except LineBindingUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "伺服器尚未設定 TOKEN_ENCRYPTION_KEY，無法使用 LINE 綁定",
        ) from None
    return LineBindCodeOut(
        code=code, expires_at=expires_at, ttl_seconds=int(CODE_TTL.total_seconds())
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line_binding(session: SessionDep, user: CurrentUser) -> Response:
    """解除綁定並撤銷那把存取權杖；沒綁定時也回 204（冪等）。LINE 沒啟用回 404。"""
    _require_enabled()
    await unbind_user(session, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
