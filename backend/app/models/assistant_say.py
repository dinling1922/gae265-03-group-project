import uuid
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.models.base import BaseTable


class AssistantSayTranslation(BaseTable, table=True):
    """小助理幫你說（#570）翻好的內容：整個行程（一張保單）存一份，不是每個事件各存一份。

    指紋相同就直接回傳 `content`，不再呼叫翻譯員；指紋不同（文件清單、開立單位、文件名稱、
    請櫃檯載明的項目或語言變了）才重新翻譯並覆蓋同一筆。翻譯失敗不寫入。保單刪除時一起刪除。
    """

    __tablename__ = "assistant_say_translation"  # pyright: ignore[reportAssignmentType]

    policy_id: uuid.UUID = Field(
        foreign_key="policy.id", ondelete="CASCADE", unique=True
    )
    fingerprint: str = Field(
        description="要翻譯的句子（句子 ID → 中文）加目標語言，排序後的 JSON 做 sha256"
    )
    language: str | None = Field(
        default=None, description="目標語言代碼；只翻英文時是空的（和 content 一致）"
    )
    content: dict[str, Any] = Field(
        sa_column=Column(JSON, nullable=False),
        description="回給前端的內容（AssistantSayOut）",
    )
