import { delay, HttpResponse, http } from "msw";
import type {
  ActivitiesCreated,
  ActivitiesIn,
  ActivityBrief,
  ActivityFailure,
  ActivityOut,
  ActivityPatch,
  Suggestion,
} from "../client/types.gen";

/** 假資料模式的日程活動（每筆比較紀錄一份），重新整理頁面就清空。 */
const mockActivities = new Map<string, ActivityOut[]>();

const list = (comparisonId: string) => mockActivities.get(comparisonId) ?? [];

const MOCK_SUGGESTIONS: Suggestion[] = [
  { concern: "突然生病就醫", reason: "D1 黑門市場吃生蠔，生食容易吃壞肚子" },
  { concern: "信用卡盜刷", reason: "D3 心齋橋藥妝大採購，刷卡次數多" },
  { concern: "不小心弄傷別人或弄壞東西", reason: "D2 租和服，可能弄壞租借品" },
];

/** 日期範圍外的活動（縮短日期時會被刪掉的那些）。 */
export function activitiesOutside(
  comparisonId: string,
  depart: string,
  returnDate: string,
): ActivityBrief[] {
  return list(comparisonId)
    .filter((a) => a.date < depart || a.date > returnDate)
    .sort((a, b) => a.date.localeCompare(b.date) || a.hour - b.hour)
    .map(({ id, date, hour, title }) => ({ id, date, hour, title }));
}

export function removeActivitiesOutside(comparisonId: string, depart: string, returnDate: string) {
  mockActivities.set(
    comparisonId,
    list(comparisonId).filter((a) => a.date >= depart && a.date <= returnDate),
  );
}

const NO_ACTIVITY = { detail: "找不到這個活動" };

/**
 * 日程活動 API 的假實作，規則與後端相同：一個時段一項、批次逐筆回報失敗。
 * rangeOf：取比較紀錄的日期範圍，找不到回 undefined（回 404）。
 */
export const itineraryHandlers = (
  rangeOf: (comparisonId: string) => { depart: string; returnDate: string } | undefined,
) => [
  http.get("*/api/v1/comparisons/:id/activities", ({ params }) => {
    const id = String(params.id);
    if (!rangeOf(id)) return HttpResponse.json({ detail: "找不到這筆比較紀錄" }, { status: 404 });
    const sorted = [...list(id)].sort((a, b) => a.date.localeCompare(b.date) || a.hour - b.hour);
    return HttpResponse.json<ActivityOut[]>(sorted);
  }),
  // 推薦的在意事項：跟後端一樣，空日程回空陣列；有活動就回固定 3 項
  http.post("*/api/v1/comparisons/:id/concern-suggestions", async ({ params }) => {
    const id = String(params.id);
    if (!rangeOf(id)) return HttpResponse.json({ detail: "找不到這筆比較紀錄" }, { status: 404 });
    await delay(600);
    return HttpResponse.json<Suggestion[]>(list(id).length === 0 ? [] : MOCK_SUGGESTIONS);
  }),
  http.post("*/api/v1/comparisons/:id/activities", async ({ params, request }) => {
    const id = String(params.id);
    const range = rangeOf(id);
    if (!range) return HttpResponse.json({ detail: "找不到這筆比較紀錄" }, { status: 404 });
    const { activities } = (await request.json()) as ActivitiesIn;
    const all = list(id);
    const created: ActivityOut[] = [];
    const failed: ActivityFailure[] = [];
    activities.forEach((item, index) => {
      if (item.date < range.depart || item.date > range.returnDate) {
        failed.push({
          index,
          code: "DATE_OUT_OF_RANGE",
          message: `${item.date} 不在這趟旅行的 ${range.depart}～${range.returnDate} 之內`,
        });
        return;
      }
      const taken = all.find((a) => a.date === item.date && a.hour === item.hour);
      if (taken) {
        failed.push({
          index,
          code: "SLOT_TAKEN",
          message: `${item.date} ${String(item.hour).padStart(2, "0")}:00 已有活動`,
          occupied_by: taken.id,
        });
        return;
      }
      const activity: ActivityOut = {
        id: crypto.randomUUID(),
        description: "",
        location: "",
        category: "explore",
        duration: 1,
        latitude: null,
        longitude: null,
        ...item,
      };
      all.push(activity);
      created.push(activity);
    });
    mockActivities.set(id, all);
    return HttpResponse.json<ActivitiesCreated>({ created, failed });
  }),
  http.patch("*/api/v1/comparisons/:id/activities/:activityId", async ({ params, request }) => {
    const id = String(params.id);
    const range = rangeOf(id);
    if (!range) return HttpResponse.json({ detail: "找不到這筆比較紀錄" }, { status: 404 });
    const all = list(id);
    const current = all.find((a) => a.id === params.activityId);
    if (!current) return HttpResponse.json(NO_ACTIVITY, { status: 404 });
    const next = { ...current, ...((await request.json()) as ActivityPatch) } as ActivityOut;
    if (next.date < range.depart || next.date > range.returnDate) {
      return HttpResponse.json({ detail: "日期不在這趟旅行之內" }, { status: 422 });
    }
    const taken = all.find(
      (a) => a.id !== current.id && a.date === next.date && a.hour === next.hour,
    );
    if (taken) {
      return HttpResponse.json(
        {
          detail: {
            code: "SLOT_TAKEN",
            message: `${next.date} ${String(next.hour).padStart(2, "0")}:00 已有活動`,
            occupied_by: taken.id,
          },
        },
        { status: 409 },
      );
    }
    mockActivities.set(
      id,
      all.map((a) => (a.id === next.id ? next : a)),
    );
    return HttpResponse.json<ActivityOut>(next);
  }),
  http.delete("*/api/v1/comparisons/:id/activities", ({ params }) => {
    const id = String(params.id);
    if (!rangeOf(id)) return HttpResponse.json({ detail: "找不到這筆比較紀錄" }, { status: 404 });
    const removed = list(id).length;
    mockActivities.set(id, []);
    return HttpResponse.json({ removed });
  }),
  http.delete("*/api/v1/comparisons/:id/activities/:activityId", ({ params }) => {
    const id = String(params.id);
    if (!rangeOf(id)) return HttpResponse.json({ detail: "找不到這筆比較紀錄" }, { status: 404 });
    if (!list(id).some((a) => a.id === params.activityId)) {
      return HttpResponse.json(NO_ACTIVITY, { status: 404 });
    }
    mockActivities.set(
      id,
      list(id).filter((a) => a.id !== params.activityId),
    );
    return new HttpResponse(null, { status: 204 });
  }),
];
