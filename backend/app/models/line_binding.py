import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, LargeBinary
from sqlmodel import Field

from app.models.base import BaseTable


class LineBinding(BaseTable, table=True):
    """LINE 綁定（ADR-0033）：一位使用者與一個 LINE 帳號的對應，兩邊各自唯一。

    `token_encrypted` 是綁定時替使用者建的存取權杖（名稱「LINE 綁定」、唯讀）的明文，用 TokenCipher 加密；
    轉接層每次呼叫 MCP 都要出示它。`api_token_id` 指向那把權杖，方便撤銷與判斷是否被撤銷、快到期。
    明文與解密後的值都不進 log。
    """

    __tablename__ = "line_binding"  # pyright: ignore[reportAssignmentType]

    user_id: uuid.UUID = Field(
        foreign_key="user.id", ondelete="CASCADE", unique=True, index=True
    )
    line_user_id: str = Field(unique=True, description="LINE 的 userId（U 開頭）")
    token_encrypted: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    key_version: int = Field(default=1, description="加密金鑰版本，方便輪替")
    api_token_id: uuid.UUID = Field(
        foreign_key="api_token.id", ondelete="CASCADE", index=True
    )
    bound_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class LineBindCode(BaseTable, table=True):
    """綁定碼（ADR-0033）：每位使用者最多一筆，重新產生就覆蓋同一列，舊碼因此立即失效（user_id 唯一，資料庫層保證）。

    只存 `code_hash`＝HMAC-SHA256(TOKEN_ENCRYPTION_KEY, 碼)，所以只拿到資料庫（備份、唯讀 SQL）也看不到碼，
    也不能離線窮舉出碼（6 位數只有 100 萬種，純雜湊一秒內可窮舉）。`code_hash` 唯一：兩個使用者不會同時持有同一組碼，
    所以「碼 → 使用者」可以直接查。用過的碼留著（`used_at`）到使用者重新產生或刪帳號，才能回覆「已用過」。
    """

    __tablename__ = "line_bind_code"  # pyright: ignore[reportAssignmentType]

    user_id: uuid.UUID = Field(
        foreign_key="user.id", ondelete="CASCADE", unique=True, index=True
    )
    code_hash: str = Field(unique=True)
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    used_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )


class LineBindFailure(BaseTable, table=True):
    """傳錯綁定碼的紀錄（防暴力猜測）：以 LINE userId 與全域兩種計數，只保留視窗內的。

    不存使用者傳來的內容。`created_at` 有索引，查詢與清理都靠它。
    """

    __tablename__ = "line_bind_failure"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_line_bind_failure_created_at", "created_at"),)

    line_user_id: str = Field(index=True)
