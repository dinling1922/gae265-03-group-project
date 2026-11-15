import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  clearItineraryActivities,
  createComparison,
  createItineraryActivities,
  deleteItineraryActivity,
  updateComparison,
  updateItineraryActivity,
} from "../../client";
import {
  getComparisonQueryKey,
  listItineraryActivitiesQueryKey,
} from "../../client/@tanstack/react-query.gen";
import type {
  ActivitiesCreated,
  ActivityBrief,
  ActivityIn,
  ActivityOut,
  ActivityPatch,
  ComparisonOut,
  Destination,
} from "../../client/types.gen";

export type TripFields = {
  destination: Destination;
  city: string;
  depart_date: string;
  return_date: string;
};

/** 動作的結果：失敗時 code 是機器可判斷的代碼（WebMCP 工具會照它組錯誤訊息），message 是給人看的。 */
export type ActionResult<T> =
  | { ok: true; data: T }
  | { ok: false; code: string; message: string; detail?: unknown };

export type SaveTripData = { comparison: ComparisonOut; removed: number };
export type WouldRemove = { activities: ActivityBrief[] };

/** 後端錯誤轉成畫面文字：422 顯示各欄位的訊息，其他顯示 detail。 */
export function errorText(error: unknown, fallback = "存檔失敗，請再試一次。"): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).join(" ");
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return fallback;
}

function failure(error: unknown, status: number | undefined): ActionResult<never> {
  const detail = (error as { detail?: { code?: string } } | null)?.detail;
  const code = detail && typeof detail === "object" && detail.code ? detail.code : `HTTP_${status}`;
  return { ok: false, code, message: errorText(error), detail };
}

/**
 * 日程頁的動作（建立／更新旅行規劃、新增／修改／移除／清除活動）：畫面上的按鈕與 WebMCP 工具共用。
 * 每個動作都等後端回應、快取更新（會重新讀取日程活動）之後才回來，呼叫端不用再等畫面。
 * `id` 是 undefined 代表還在 `/compare/new`，還沒有比較紀錄。
 */
export function useItineraryActions(id: string | undefined) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const path = { comparison_id: id ?? "" };

  const refreshActivities = () =>
    qc.invalidateQueries({ queryKey: listItineraryActivitiesQueryKey({ path }) });

  /** 存旅行規劃。沒有 id 就建立並把網址換成日程頁；縮短日期會刪活動時，沒帶 confirmRemove 回 WOULD_REMOVE_ACTIVITIES。 */
  async function saveTrip(
    fields: TripFields,
    confirmRemove = false,
  ): Promise<ActionResult<SaveTripData>> {
    if (!id) {
      const { data, error, response } = await createComparison({ body: fields });
      if (!data) return failure(error, response?.status);
      qc.setQueryData(getComparisonQueryKey({ path: { comparison_id: data.id } }), data);
      await navigate({ to: "/compare/$id/itinerary", params: { id: data.id }, replace: true });
      return { ok: true, data: { comparison: data, removed: 0 } };
    }
    const { data, error, response } = await updateComparison({
      path,
      body: { ...fields, confirm_remove: confirmRemove },
    });
    if (!data) return failure(error, response?.status);
    qc.setQueryData(getComparisonQueryKey({ path }), data);
    const removed = data.removed_activity_count ?? 0;
    if (removed > 0) await refreshActivities();
    return { ok: true, data: { comparison: data, removed } };
  }

  /** 批次新增；時段被佔用或日期超出範圍的那筆在 `failed`，其他照常存。 */
  async function addActivities(activities: ActivityIn[]): Promise<ActionResult<ActivitiesCreated>> {
    const { data, error, response } = await createItineraryActivities({
      path,
      body: { activities },
    });
    if (!data) return failure(error, response?.status);
    await refreshActivities();
    return { ok: true, data };
  }

  async function changeActivity(
    activityId: string,
    patch: ActivityPatch,
  ): Promise<ActionResult<ActivityOut>> {
    const { data, error, response } = await updateItineraryActivity({
      path: { ...path, activity_id: activityId },
      body: patch,
    });
    if (!data) return failure(error, response?.status);
    await refreshActivities();
    return { ok: true, data };
  }

  async function removeActivity(activityId: string): Promise<ActionResult<null>> {
    const { error, response } = await deleteItineraryActivity({
      path: { ...path, activity_id: activityId },
    });
    if (!response?.ok) return failure(error, response?.status);
    await refreshActivities();
    return { ok: true, data: null };
  }

  /** 清除這趟旅行的所有活動（旅行規劃不動）；回傳刪了幾個。 */
  async function clearActivities(): Promise<ActionResult<{ removed: number }>> {
    const { data, error, response } = await clearItineraryActivities({ path });
    if (!data) return failure(error, response?.status);
    await refreshActivities();
    return { ok: true, data };
  }

  return { saveTrip, addActivities, changeActivity, removeActivity, clearActivities };
}
