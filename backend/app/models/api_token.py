import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field

from app.models.base import BaseTable


class ApiToken(BaseTable, table=True):
    """存取權杖（ADR-0032）：使用者交給外部代理呼叫 MCP 伺服器的憑證。

    只存明文的 SHA-256 雜湊（權杖是高熵隨機字串，不需要慢雜湊）；明文只在建立時回傳一次。
    prefix 是明文開頭幾個字，給使用者在列表上分辨；scopes 目前只有 read。
    """

    __tablename__ = "api_token"  # pyright: ignore[reportAssignmentType]

    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    name: str = Field(description="使用者取的名字，例如「我的 Claude Desktop」")
    token_hash: str = Field(unique=True, description="明文的 SHA-256（十六進位）")
    prefix: str = Field(description="明文開頭，列表上分辨用")
    scopes: list[str] = Field(
        default_factory=lambda: ["read"],
        sa_column=Column(ARRAY(Text()), nullable=False),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    last_used_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    revoked_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
