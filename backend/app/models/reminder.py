import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, UniqueConstraint
from sqlmodel import Field

from app.models.base import BaseTable


class ReminderLetter(BaseTable, table=True):
    """寄出的提醒信（ADR-0031）：每寄出一封記一筆，給行程頁的寄送進度與輪次判斷用。

    寄信工作在同一個交易裡寫入這一筆並把 `policy.reminder_count` 加 1。
    不用來防止重複寄信（那是鎖住保單與計數的工作）；保單刪除時一起刪除。
    """

    __tablename__ = "reminder_letter"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("policy_id", "round_started_at", "number"),
        CheckConstraint("number BETWEEN 1 AND 3", name="number_valid"),
    )

    policy_id: uuid.UUID = Field(
        foreign_key="policy.id", ondelete="CASCADE", index=True
    )
    round_started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="這一封所屬那一輪的起點（同當時的 policy.reminder_started_at）",
    )
    number: int = Field(description="這一輪的第幾封（1～3）")
    planned_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="依規則這一封預計寄出的時間",
    )
    sent_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="實際寄出（SMTP 收下）的時間；信件的 Date 標頭也用這個時間",
    )
