import uuid
from datetime import date

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.models.base import BaseTable


class ItineraryActivity(BaseTable, table=True):
    """日程活動：某一天某個整點開始的一項安排（ADR-0031）。

    一筆比較紀錄恰好一份日程，所以活動直接掛在比較紀錄下；同一個時段只能有一項。
    分類與時段範圍由 API 用 enum／範圍驗證，不加資料庫 CHECK。
    """

    __tablename__ = "itinerary_activity"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (UniqueConstraint("comparison_id", "date", "hour"),)

    comparison_id: uuid.UUID = Field(
        foreign_key="comparison.id", ondelete="CASCADE", index=True
    )
    date: date
    hour: int = Field(description="開始的整點，7–22")
    title: str
    description: str = Field(default="", sa_column_kwargs={"server_default": ""})
    location: str = Field(default="", sa_column_kwargs={"server_default": ""})
    category: str = Field(
        description="分類代碼（app.domain.itinerary.ActivityCategory）"
    )
    duration: float = Field(description="時長（小時），0.5–8，0.5 一級")
    latitude: float | None = Field(default=None, description="沒有座標就不上地圖")
    longitude: float | None = None
