"""存取權杖管理（ADR-0032、#471）：建立、列出、撤銷自己的權杖；用 cookie session 認證。

建立時回傳明文，只此一次；列出與其他讀取都不含明文或雜湊。
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.api.deps import CurrentUser, SessionDep
from app.domain.access_tokens import (
    MAX_ACTIVE_TOKENS,
    TokenLimitError,
    TokenStatus,
    create_token,
    list_tokens,
    revoke_token,
    token_status,
)
from app.models import ApiToken

router = APIRouter(prefix="/me/api-tokens", tags=["api-tokens"])


class ApiTokenCreate(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
    ]
    scopes: list[Literal["read", "write"]] = Field(
        default=["read"],
        description='權限範圍：只能是 ["read"]（預設，唯讀）或 ["read", "write"]（可建立資料）；只有 write 或空的回 422',
    )

    @field_validator("scopes")
    @classmethod
    def _read_required(cls, v: list[str]) -> list[str]:
        if "read" not in v:
            raise ValueError("write 權杖必須同時包含 read")
        return sorted(set(v), key=("read", "write").index)


class ApiTokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    status: TokenStatus

    @classmethod
    def of(cls, token: ApiToken) -> ApiTokenOut:
        return cls(
            id=token.id,
            name=token.name,
            prefix=token.prefix,
            scopes=token.scopes,
            created_at=token.created_at,
            last_used_at=token.last_used_at,
            expires_at=token.expires_at,
            revoked_at=token.revoked_at,
            status=token_status(token),
        )


class ApiTokenCreated(ApiTokenOut):
    token: str = Field(description="權杖明文；只在建立時回傳這一次")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_token(
    body: ApiTokenCreate, session: SessionDep, user: CurrentUser
) -> ApiTokenCreated:
    """建立存取權杖；scopes 預設唯讀，不合法的組合回 422；有效權杖已滿 5 把回 409。"""
    try:
        row, plaintext = await create_token(session, user.id, body.name, body.scopes)
    except TokenLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"有效的存取權杖最多 {MAX_ACTIVE_TOKENS} 把，請先撤銷不用的",
        ) from None
    return ApiTokenCreated(**ApiTokenOut.of(row).model_dump(), token=plaintext)


@router.get("")
async def list_api_tokens(session: SessionDep, user: CurrentUser) -> list[ApiTokenOut]:
    """自己的所有存取權杖（含已撤銷與過期），最新在前。"""
    return [ApiTokenOut.of(t) for t in await list_tokens(session, user.id)]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Response:
    """撤銷自己的權杖，下一次驗證立即失效；不存在或不是自己的回 404。"""
    if not await revoke_token(session, user.id, token_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這把權杖")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
