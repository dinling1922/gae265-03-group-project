"""日程活動（ADR-0031）：比較紀錄底下逐日逐時的安排；只能存取自己的比較紀錄。

一個時段（日期＋整點）只能有一項。日期必須在比較紀錄的出發、回程日之間。
批次新增時各筆互不影響：成功的存起來，失敗的逐筆回報原因。
"""

import uuid
from datetime import date as Date
from typing import Literal, Self

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import delete
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.comparisons import own_comparison
from app.domain.itinerary import FIRST_HOUR, LAST_HOUR, ActivityCategory
from app.models import Comparison, ItineraryActivity

router = APIRouter(tags=["itinerary"])

MAX_BATCH = 30


class ActivityIn(BaseModel):
    date: Date
    hour: int = Field(ge=FIRST_HOUR, le=LAST_HOUR, description="開始的整點，24 小時制")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    location: str = Field(default="", max_length=200)
    category: ActivityCategory = ActivityCategory.EXPLORE
    duration: float = Field(
        default=1, ge=0.5, le=8, multiple_of=0.5, description="時長（小時），0.5 一級"
    )
    latitude: float | None = Field(default=None, ge=-85, le=85)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _check_coordinates(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise PydanticCustomError(
                "coordinates_pair", "緯度與經度要一起填，或都不填。"
            )
        return self


class ActivityPatch(BaseModel):
    """只帶要改的欄位；合併已存的值之後照 ActivityIn 的規則驗證。座標帶 null 表示清除。"""

    date: Date | None = None
    hour: int | None = None
    title: str | None = None
    description: str | None = None
    location: str | None = None
    category: ActivityCategory | None = None
    duration: float | None = None
    latitude: float | None = None
    longitude: float | None = None


class ActivityOut(BaseModel):
    id: uuid.UUID
    date: Date
    hour: int
    title: str
    description: str
    location: str
    category: ActivityCategory
    duration: float
    latitude: float | None
    longitude: float | None


class ActivitiesIn(BaseModel):
    activities: list[ActivityIn] = Field(min_length=1, max_length=MAX_BATCH)


class ActivityFailure(BaseModel):
    index: int = Field(description="這筆在請求陣列裡的位置（從 0 起算）")
    code: Literal["SLOT_TAKEN", "DATE_OUT_OF_RANGE"]
    message: str
    occupied_by: uuid.UUID | None = Field(
        default=None, description="SLOT_TAKEN 時，佔用這個時段的活動 id"
    )


class ActivitiesCreated(BaseModel):
    created: list[ActivityOut]
    failed: list[ActivityFailure]


class ActivitiesCleared(BaseModel):
    removed: int = Field(description="刪掉幾個活動")


class SlotTaken(BaseModel):
    code: Literal["SLOT_TAKEN"]
    message: str
    occupied_by: uuid.UUID


class SlotTakenConflict(BaseModel):
    """409 的內容（FastAPI 把 HTTPException 的 detail 包在 detail 底下）。"""

    detail: SlotTaken


def _out_of_range(c: Comparison, d: Date) -> str | None:
    if c.depart_date <= d <= c.return_date:
        return None
    return f"{d.isoformat()} 不在這趟旅行的 {c.depart_date.isoformat()}～{c.return_date.isoformat()} 之內"


async def _own_activity(
    session: SessionDep, comparison: Comparison, activity_id: uuid.UUID
) -> ItineraryActivity:
    activity = await session.get(ItineraryActivity, activity_id)
    if activity is None or activity.comparison_id != comparison.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這個活動")
    return activity


async def _occupant(
    session: SessionDep, comparison_id: uuid.UUID, d: Date, hour: int
) -> uuid.UUID | None:
    return (
        await session.exec(
            select(ItineraryActivity.id).where(
                ItineraryActivity.comparison_id == comparison_id,
                ItineraryActivity.date == d,
                ItineraryActivity.hour == hour,
            )
        )
    ).first()


@router.get("/comparisons/{comparison_id}/activities")
async def list_itinerary_activities(
    session: SessionDep, user: CurrentUser, comparison_id: uuid.UUID
) -> list[ActivityOut]:
    """這趟旅行的所有日程活動，依日期、時段排序。"""
    comparison = await own_comparison(session, user, comparison_id)
    rows = await session.exec(
        select(ItineraryActivity)
        .where(ItineraryActivity.comparison_id == comparison.id)
        .order_by(col(ItineraryActivity.date), col(ItineraryActivity.hour))
    )
    return [ActivityOut.model_validate(a, from_attributes=True) for a in rows]


@router.post("/comparisons/{comparison_id}/activities")
async def create_itinerary_activities(
    session: SessionDep,
    user: CurrentUser,
    comparison_id: uuid.UUID,
    body: ActivitiesIn,
) -> ActivitiesCreated:
    """批次新增，最多 30 筆；時段被佔用（含同一批裡先出現的）或日期超出範圍的那筆失敗，其他照常存。"""
    comparison = await own_comparison(session, user, comparison_id)
    taken: dict[tuple[Date, int], uuid.UUID] = {
        (a.date, a.hour): a.id
        for a in await session.exec(
            select(ItineraryActivity).where(
                ItineraryActivity.comparison_id == comparison.id
            )
        )
    }
    created: list[ItineraryActivity] = []
    failed: list[ActivityFailure] = []
    for index, item in enumerate(body.activities):
        if message := _out_of_range(comparison, item.date):
            failed.append(
                ActivityFailure(index=index, code="DATE_OUT_OF_RANGE", message=message)
            )
        elif occupant := taken.get((item.date, item.hour)):
            failed.append(
                ActivityFailure(
                    index=index,
                    code="SLOT_TAKEN",
                    message=f"{item.date.isoformat()} {item.hour:02d}:00 已有活動",
                    occupied_by=occupant,
                )
            )
        else:
            activity = ItineraryActivity(
                comparison_id=comparison.id, **item.model_dump()
            )
            taken[(item.date, item.hour)] = activity.id
            session.add(activity)
            created.append(activity)
    # ponytail: 兩個請求同時搶同一個時段時，後到的會撞唯一限制回 500；有人會同時操作再處理
    await session.commit()
    return ActivitiesCreated(
        created=[ActivityOut.model_validate(a, from_attributes=True) for a in created],
        failed=failed,
    )


@router.patch(
    "/comparisons/{comparison_id}/activities/{activity_id}",
    responses={
        status.HTTP_409_CONFLICT: {
            "model": SlotTakenConflict,
            "description": "要移到的時段已有活動",
        }
    },
)
async def update_itinerary_activity(
    session: SessionDep,
    user: CurrentUser,
    comparison_id: uuid.UUID,
    activity_id: uuid.UUID,
    body: ActivityPatch,
) -> ActivityOut:
    """修改內容或移到其他日子與時段；可只帶部分欄位。"""
    comparison = await own_comparison(session, user, comparison_id)
    activity = await _own_activity(session, comparison, activity_id)
    stored = activity.model_dump(include=set(ActivityIn.model_fields))
    try:
        merged = ActivityIn.model_validate(stored | body.model_dump(exclude_unset=True))
    except ValidationError as e:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in e.errors()]
        ) from e
    if message := _out_of_range(comparison, merged.date):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)
    occupant = await _occupant(session, comparison.id, merged.date, merged.hour)
    if occupant is not None and occupant != activity.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            SlotTaken(
                code="SLOT_TAKEN",
                message=f"{merged.date.isoformat()} {merged.hour:02d}:00 已有活動",
                occupied_by=occupant,
            ).model_dump(mode="json"),
        )
    activity.sqlmodel_update(merged.model_dump())
    session.add(activity)
    await session.commit()
    return ActivityOut.model_validate(activity, from_attributes=True)


@router.delete(
    "/comparisons/{comparison_id}/activities/{activity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_itinerary_activity(
    session: SessionDep,
    user: CurrentUser,
    comparison_id: uuid.UUID,
    activity_id: uuid.UUID,
) -> Response:
    comparison = await own_comparison(session, user, comparison_id)
    await session.delete(await _own_activity(session, comparison, activity_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/comparisons/{comparison_id}/activities")
async def clear_itinerary_activities(
    session: SessionDep, user: CurrentUser, comparison_id: uuid.UUID
) -> ActivitiesCleared:
    """清除這趟旅行的所有日程活動（旅行規劃本身不動）；沒有活動也回 0。"""
    comparison = await own_comparison(session, user, comparison_id)
    result = await session.exec(
        delete(ItineraryActivity).where(
            col(ItineraryActivity.comparison_id) == comparison.id
        )
    )
    await session.commit()
    return ActivitiesCleared(removed=result.rowcount)
