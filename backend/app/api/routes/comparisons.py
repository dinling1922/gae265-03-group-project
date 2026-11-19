"""比較紀錄（spec #75）：1-1、1-2 換步驟時存檔、進入時讀回。只能存取自己的紀錄。

存檔時天數或在意事項有變、或日程改了（推薦被清空）就重算推薦（spec #457、ADR-0034），
只存需求等級、兩個方案與各自選的保額；保費、額度、推薦理由與比較表在讀取時重算。
"""

import logging
import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError
from sqlalchemy import or_
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.domain.benefits import Concern
from app.domain.concern_suggestions import (
    Suggester,
    Suggestion,
    get_concern_suggester,
    validated,
)
from app.domain.destinations import Destination
from app.domain.need_levels import NeedLeveler, get_need_leveler, judge
from app.domain.plans import get_plan
from app.domain.recommendation import (
    Benefit,
    ConcernLevel,
    PlanCardOut,
    Quotes,
    RecommendationOut,
    Source,
    build,
    pick,
)
from app.models import Comparison, ItineraryActivity, PlanBenefit, PlanPremium

router = APIRouter(tags=["comparisons"])
logger = logging.getLogger(__name__)


def _check_unique(concerns: list[Concern]) -> list[Concern]:
    if len(set(concerns)) != len(concerns):
        raise PydanticCustomError("concerns_unique", "在意事項不能重複。")
    return concerns


class ComparisonIn(BaseModel):
    destination: Destination
    city: str = Field(default="", max_length=30, description="城市，選填")
    depart_date: date
    return_date: date
    concerns: Annotated[list[Concern], AfterValidator(_check_unique)] = Field(
        default=[], max_length=3, description="依重要程度排序，最多 3 項、不重複"
    )

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        # 出發日可以是過去：app 不實際投保，只拿來比較與試算
        if self.return_date < self.depart_date:
            raise PydanticCustomError("return_before_depart", "回程日不能早於出發日。")
        days = (self.return_date - self.depart_date).days + 1
        # 只有 4–10 天有報價（ADR-0018）
        if days < 4:
            raise PydanticCustomError("trip_too_short", "旅遊天數最短 4 天。")
        if days > 10:
            raise PydanticCustomError("trip_too_long", "旅遊天數最長 10 天。")
        return self


class ComparisonPatch(BaseModel):
    """只帶要改的欄位；合併已存的值之後照 ComparisonIn 的規則驗證。"""

    destination: Destination | None = None
    city: str | None = None
    depart_date: date | None = None
    return_date: date | None = None
    concerns: list[Concern] | None = None
    confirm_remove: bool = Field(
        default=False,
        description="縮短日期會刪除日程活動時，帶 true 表示使用者已同意；沒帶會回 409",
    )
    recommended_sum_insured: int | None = Field(
        default=None,
        description="改推薦方案卡片的身故保額（元）；同一請求改了天數或在意事項時忽略",
    )
    alternative_sum_insured: int | None = Field(
        default=None,
        description="改對照方案（次推薦）卡片的身故保額（元）；同一請求改了天數或在意事項時忽略",
    )


class ComparisonOut(BaseModel):
    id: uuid.UUID
    destination: Destination
    city: str
    depart_date: date
    return_date: date
    concerns: list[Concern]
    created_at: datetime
    updated_at: datetime
    removed_activity_count: int = Field(
        default=0, description="這次修改刪除的日程活動數（縮短日期並帶 confirm_remove）"
    )
    recommendation: RecommendationOut | None = Field(
        description="推薦方案、次推薦與比較表；還沒推薦（在意事項是空的）時是 null"
    )


async def own_comparison(
    session: SessionDep, user: CurrentUser, comparison_id: uuid.UUID
) -> Comparison:
    comparison = await session.get(Comparison, comparison_id)
    # 別人的紀錄跟不存在一樣回 404，不洩漏它存在
    if comparison is None or comparison.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這筆比較紀錄")
    return comparison


def trip_days(c: Comparison) -> int:
    return (c.return_date - c.depart_date).days + 1


async def load_quotes(session: SessionDep, days: int) -> Quotes:
    premiums = (
        await session.exec(select(PlanPremium).where(PlanPremium.days == days))
    ).all()
    # ponytail: 每次讀整張 plan_benefit（約兩千列），變慢再依方案與保額篩
    benefits = (
        await session.exec(select(PlanBenefit).order_by(col(PlanBenefit.position)))
    ).all()
    by_plan: dict[str, dict[int, int]] = {}
    for p in premiums:
        by_plan.setdefault(p.plan_code, {})[p.sum_insured] = p.premium
    by_pair: dict[tuple[str, int], list[Benefit]] = {}
    for b in benefits:
        by_pair.setdefault((b.plan_code, b.sum_insured), []).append(
            Benefit(b.name, b.category, b.amount)
        )
    return Quotes(by_plan, by_pair)


LevelerDep = Annotated[NeedLeveler, Depends(get_need_leveler)]


async def _activities(
    session: SessionDep, comparison: Comparison
) -> list[ItineraryActivity]:
    return list(
        (
            await session.exec(
                select(ItineraryActivity)
                .where(ItineraryActivity.comparison_id == comparison.id)
                .order_by(col(ItineraryActivity.date), col(ItineraryActivity.hour))
            )
        ).all()
    )


async def _recommend(
    session: SessionDep, comparison: Comparison, leveler: NeedLeveler
) -> None:
    """重算需求等級與推薦，兩張卡片的保額回到各自的推薦保額；在意事項是空的時清空。"""
    clear_recommendation(comparison)
    if not comparison.concerns:
        return
    days = trip_days(comparison)
    quotes = await load_quotes(session, days)
    activities = await _activities(session, comparison)
    levels, source = await judge(comparison, activities, leveler, days)
    picked = pick(quotes, levels)
    if picked is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{days} 天有報價的方案不到兩個，無法推薦（請先 make seed）",
        )
    rec, alt = picked
    comparison.recommended_plan_code, comparison.recommended_sum_insured = rec
    comparison.alternative_plan_code, comparison.alternative_sum_insured = alt
    comparison.concern_levels = [x.model_dump(mode="json") for x in levels]
    comparison.recommendation_source = source


def clear_recommendation(c: Comparison) -> None:
    """清掉推薦（兩個方案與保額、需求等級與來源）；下次 1-2 存檔時補算。"""
    c.recommended_plan_code = c.recommended_sum_insured = None
    c.alternative_plan_code = c.alternative_sum_insured = None
    c.concern_levels = c.recommendation_source = None


def _levels(c: Comparison) -> tuple[list[ConcernLevel], Source]:
    """存下的需求等級與來源；有推薦就一定有（舊規則的推薦已由 migration 清空）。"""
    assert c.concern_levels is not None
    levels = [ConcernLevel.model_validate(x) for x in c.concern_levels]
    return levels, "llm" if c.recommendation_source == "llm" else "default"


def _saved_plans(c: Comparison) -> tuple[tuple[str, int], tuple[str, int]] | None:
    """存下的 (推薦方案, 保額)、(對照方案, 保額)；還沒推薦時是 None。"""
    if (
        c.recommended_plan_code
        and c.recommended_sum_insured
        and c.alternative_plan_code
        and c.alternative_sum_insured
    ):
        return (c.recommended_plan_code, c.recommended_sum_insured), (
            c.alternative_plan_code,
            c.alternative_sum_insured,
        )
    return None


async def plan_cards(
    session: SessionDep, comparison: Comparison
) -> tuple[Quotes, list[PlanCardOut]]:
    """1-4 用的推薦、次推薦卡片（保額沒有報價時回到推薦保額，同 1-3）；還沒推薦就 422。"""
    saved = _saved_plans(comparison)
    if saved is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "還沒有推薦方案，請先選在意事項"
        )
    quotes = await load_quotes(session, trip_days(comparison))
    rec = build(quotes, *_levels(comparison), *saved)
    return quotes, [rec.recommended, rec.alternative]


async def _out(session: SessionDep, comparison: Comparison) -> ComparisonOut:
    recommendation = None
    if saved := _saved_plans(comparison):
        recommendation = build(
            await load_quotes(session, trip_days(comparison)),
            *_levels(comparison),
            *saved,
        )
    return ComparisonOut.model_validate(
        comparison.model_dump() | {"recommendation": recommendation}
    )


async def _change_sums(
    session: SessionDep, comparison: Comparison, body: ComparisonPatch
) -> None:
    """改卡片的保額：只改該卡片，不重新推薦；保額要是該方案在該天數下有報價的。"""
    cards = [
        (
            "recommended_sum_insured",
            comparison.recommended_plan_code,
            body.recommended_sum_insured,
        ),
        (
            "alternative_sum_insured",
            comparison.alternative_plan_code,
            body.alternative_sum_insured,
        ),
    ]
    cards = [c for c in cards if c[2] is not None]
    if not cards:
        return
    if comparison.recommended_plan_code is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "還沒有推薦方案，請先選在意事項"
        )
    quotes = await load_quotes(session, trip_days(comparison))
    for field, plan_code, sum_insured in cards:
        assert plan_code is not None and sum_insured is not None
        if sum_insured not in quotes.options(plan_code):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"這個方案沒有 {sum_insured / 10000:g} 萬的報價",
            )
        setattr(comparison, field, sum_insured)


class PastPlanOut(BaseModel):
    plan_id: str
    name: str
    company: str
    sum_insured: int = Field(description="當時卡片選的身故保額（元）")


class PastComparisonOut(BaseModel):
    """功能二「從過去比較帶入」用的摘要：只有目的地、日期與當時推薦的兩個方案。"""

    id: uuid.UUID
    destination: Destination
    depart_date: date
    return_date: date
    days: int
    recommended: PastPlanOut
    alternative: PastPlanOut


def _past_plan(code: str, sum_insured: int) -> PastPlanOut | None:
    plan = get_plan(code)
    if plan is None:  # 方案已從設定移除，帶入沒有意義
        return None
    return PastPlanOut(
        plan_id=plan.id, name=plan.name, company=plan.company, sum_insured=sum_insured
    )


@router.get("/comparisons")
async def list_comparisons(
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=20)] = 3,
) -> list[PastComparisonOut]:
    """我最近推薦過的比較（新的在前），最多 limit 筆；還沒推薦出方案的不列。"""
    rows = await session.exec(
        select(Comparison)
        .where(
            Comparison.user_id == user.id,
            col(Comparison.recommended_plan_code).is_not(None),
            col(Comparison.alternative_plan_code).is_not(None),
        )
        .order_by(col(Comparison.created_at).desc())
    )
    out: list[PastComparisonOut] = []
    for c in rows:
        saved = _saved_plans(c)
        if saved is None:
            continue
        rec, alt = _past_plan(*saved[0]), _past_plan(*saved[1])
        if rec is None or alt is None:
            continue
        out.append(
            PastComparisonOut(
                id=c.id,
                destination=Destination(c.destination),
                depart_date=c.depart_date,
                return_date=c.return_date,
                days=trip_days(c),
                recommended=rec,
                alternative=alt,
            )
        )
        if len(out) == limit:
            break
    return out


@router.post("/comparisons", status_code=status.HTTP_201_CREATED)
async def create_comparison(
    session: SessionDep, user: CurrentUser, body: ComparisonIn, leveler: LevelerDep
) -> ComparisonOut:
    """1-1 第一次按「下一步」時建立。"""
    comparison = Comparison(user_id=user.id, **body.model_dump())
    await _recommend(session, comparison, leveler)
    session.add(comparison)
    await session.commit()
    return await _out(session, comparison)


class RecentRecommended(BaseModel):
    plan_id: str
    name: str
    company: str
    sum_insured: int = Field(description="推薦卡片目前選的身故保額（元）")


class RecentComparisonOut(BaseModel):
    id: uuid.UUID
    destination: Destination
    depart_date: date
    return_date: date
    days: int
    recommended: RecentRecommended | None = Field(
        description="推薦方案；還沒推薦或方案已從設定移除時是 null"
    )


@router.get("/comparisons/recent")
async def list_recent_comparisons(
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> list[RecentComparisonOut]:
    """首頁「最近的比較」：自己的比較紀錄，依最後操作時間（updated_at）由新到舊。

    只列摘要，不讀報價、不重算推薦。必須註冊在 `/comparisons/{comparison_id}` 前面。
    推薦方案已從設定移除（`get_plan` 查無）時 `recommended` 是 null，前端顯示「填寫中」。
    """
    query = (
        select(Comparison)
        .where(Comparison.user_id == user.id)
        .order_by(col(Comparison.updated_at).desc(), col(Comparison.id).desc())
        .limit(limit)
    )
    out = []
    for c in (await session.exec(query)).all():
        recommended = None
        if saved := _saved_plans(c):
            (plan_code, sum_insured), _ = saved
            if plan := get_plan(plan_code):
                recommended = RecentRecommended(
                    plan_id=plan.id,
                    name=plan.name,
                    company=plan.company,
                    sum_insured=sum_insured,
                )
        out.append(
            RecentComparisonOut(
                id=c.id,
                destination=Destination(c.destination),
                depart_date=c.depart_date,
                return_date=c.return_date,
                days=trip_days(c),
                recommended=recommended,
            )
        )
    return out


@router.get("/comparisons/{comparison_id}")
async def get_comparison(
    session: SessionDep, user: CurrentUser, comparison_id: uuid.UUID
) -> ComparisonOut:
    """額度是試算表顯示的金額，不是保證的理賠上限；未列的保障不等於沒有保障。"""
    comparison = await own_comparison(session, user, comparison_id)
    return await _out(session, comparison)


class ActivityBrief(BaseModel):
    id: uuid.UUID
    date: date
    hour: int
    title: str


class WouldRemoveActivities(BaseModel):
    code: Literal["WOULD_REMOVE_ACTIVITIES"]
    message: str
    activities: list[ActivityBrief]


class WouldRemoveConflict(BaseModel):
    """409 的內容（FastAPI 把 HTTPException 的 detail 包在 detail 底下）。"""

    detail: WouldRemoveActivities


@router.patch(
    "/comparisons/{comparison_id}",
    responses={
        status.HTTP_409_CONFLICT: {
            "model": WouldRemoveConflict,
            "description": "縮短日期會刪除日程活動，要帶 confirm_remove: true 才會執行",
        }
    },
)
async def update_comparison(
    session: SessionDep,
    user: CurrentUser,
    comparison_id: uuid.UUID,
    body: ComparisonPatch,
    leveler: LevelerDep,
) -> ComparisonOut:
    """換步驟時存檔，可只帶部分欄位；也用來改卡片的保額（只改該卡片，不重新推薦）。

    日期縮短而範圍外有日程活動時，沒帶 `confirm_remove: true` 回 409 並列出會刪除的活動、
    什麼都不改；帶了就在同一個 transaction 刪除，回應附刪除數量（ADR-0031）。
    """
    comparison = await own_comparison(session, user, comparison_id)
    stored = comparison.model_dump(include=set(ComparisonIn.model_fields))
    before = (trip_days(comparison), comparison.concerns)
    try:
        merged = ComparisonIn.model_validate(
            stored
            | body.model_dump(
                exclude_unset=True, include=set(ComparisonIn.model_fields)
            )
        )
    except ValidationError as e:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in e.errors()]
        ) from e
    outside = (
        await session.exec(
            select(ItineraryActivity)
            .where(
                ItineraryActivity.comparison_id == comparison.id,
                or_(
                    col(ItineraryActivity.date) < merged.depart_date,
                    col(ItineraryActivity.date) > merged.return_date,
                ),
            )
            .order_by(col(ItineraryActivity.date), col(ItineraryActivity.hour))
        )
    ).all()
    if outside and not body.confirm_remove:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            WouldRemoveActivities(
                code="WOULD_REMOVE_ACTIVITIES",
                message=f"縮短日期會移除 {len(outside)} 個活動，確認後帶 confirm_remove: true",
                activities=[
                    ActivityBrief.model_validate(a, from_attributes=True)
                    for a in outside
                ],
            ).model_dump(mode="json"),
        )
    for activity in outside:
        await session.delete(activity)
    trip_fields = {"destination", "city", "depart_date", "return_date"}
    if merged.model_dump(include=trip_fields) != comparison.model_dump(
        include=trip_fields
    ):
        comparison.concern_suggestions = None  # 旅行規劃改了：推薦的在意事項重算
    comparison.sqlmodel_update(merged.model_dump())
    changed = (trip_days(comparison), comparison.concerns) != before
    # 舊紀錄有在意事項卻沒推薦（推薦功能上線前存的）也補算
    if changed or (comparison.concerns and comparison.recommended_plan_code is None):
        await _recommend(session, comparison, leveler)
    else:
        await _change_sums(session, comparison, body)
    session.add(comparison)
    await session.commit()
    out = await _out(session, comparison)
    out.removed_activity_count = len(outside)
    return out


@router.post("/comparisons/{comparison_id}/concern-suggestions")
async def suggest_concerns(
    session: SessionDep,
    user: CurrentUser,
    comparison_id: uuid.UUID,
    suggest: Annotated[Suggester, Depends(get_concern_suggester)],
) -> list[Suggestion]:
    """依旅遊日程推薦 3 項在意事項（spec #427），依關聯強度排序。

    有存好的結果就直接回傳；日程是空的回空陣列、不呼叫 LLM。
    LLM 失敗或輸出不合格時回空陣列、不存，下次進來再試。
    """
    comparison = await own_comparison(session, user, comparison_id)
    if comparison.concern_suggestions is not None:
        return [Suggestion.model_validate(s) for s in comparison.concern_suggestions]
    activities = await _activities(session, comparison)
    if not activities:
        return []
    try:
        suggestions = validated(await suggest(comparison, activities))
    except Exception:  # LLM 失敗不該擋住 1-2：沒有推薦區塊而已
        logger.exception("推薦在意事項失敗（comparison %s）", comparison.id)
        return []
    comparison.concern_suggestions = [s.model_dump(mode="json") for s in suggestions]
    session.add(comparison)
    await session.commit()
    return suggestions
