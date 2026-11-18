from sqlalchemy import Index
from sqlmodel import Field

from app.models.base import BaseTable


class LineEvent(BaseTable, table=True):
    """LINE webhook 已處理的事件（ADR-0033）：LINE 會重送，靠 `event_key` 唯一擋掉重複處理。

    `event_key` 是 LINE 的 `webhookEventId`；群組／聊天室「只回一次說明」也用同一張表，
    key 是 `notice:<groupId 或 roomId>`。只存 key，不存訊息內容，不累積個資。
    資料列只需要保留到 LINE 不再重送為止（約一天內），`created_at` 有索引，之後可定期刪舊的。
    """

    __tablename__ = "line_event"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_line_event_created_at", "created_at"),)

    event_key: str = Field(unique=True)
