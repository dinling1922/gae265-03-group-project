import uuid

from sqlmodel import Field

from app.models.base import BaseTable


class McpCallLog(BaseTable, table=True):
    """MCP 工具呼叫的稽核紀錄（ADR-0032）：只記哪把權杖、哪個工具、何時（created_at）、成功與否。

    刻意不存輸入與輸出，稽核本身不累積個資。權杖被刪時紀錄一併刪除。
    """

    __tablename__ = "mcp_call_log"  # pyright: ignore[reportAssignmentType]

    token_id: uuid.UUID = Field(
        foreign_key="api_token.id", ondelete="CASCADE", index=True
    )
    tool_name: str
    success: bool
