import { useRef } from "react";
import { useWebMCP } from "use-webmcp-tool";
import type {
  ActivityCategory,
  ActivityIn,
  ActivityOut,
  ActivityPatch,
  ComparisonOut,
  Destination,
} from "../../client/types.gen";
import { type MapScope, mapStops, type useMapView } from "./mapView";
import {
  DESTINATIONS,
  datesBetween,
  FIRST_HOUR,
  formatHour,
  formatMD,
  HOURS,
  LAST_HOUR,
  tripDays,
  validateTrip,
  weekdayOf,
} from "./tripRules";
import type { useItineraryActions } from "./useItineraryActions";
import { type DayRef, resolveDay, resolveTripDate } from "./webmcpDates";
import { TOOL_SCHEMAS } from "./webmcpSchemas";

/** 日程頁交給工具的東西。每次渲染都是最新的；工具透過 ref 讀，所以 await 之後讀到的不是舊的。 */
export type WebMCPContext = {
  comparison: ComparisonOut | undefined;
  activities: ActivityOut[];
  view: ReturnType<typeof useMapView>;
  actions: ReturnType<typeof useItineraryActions>;
  /** 把存好的旅行規劃寫回左欄表單。 */
  syncForm: (comparison: ComparisonOut) => void;
  toast: (text: string) => void;
};

/** 每個工具回應的字元上限：模型要讀完整個回應，太長會擠掉其他內容。 */
export const RESPONSE_LIMIT = 1500;

const NO_TRIP = "請先用 set_trip_details 設定目的地與日期。";

/** 等條件成立（資料與畫面更新完）才回傳；用 setTimeout，背景分頁也會跑。 */
async function waitFor(condition: () => unknown, ms = 4000): Promise<void> {
  const until = Date.now() + ms;
  while (!condition() && Date.now() < until) await new Promise((r) => setTimeout(r, 25));
}

const when = (date: string, hour: number) => `${formatMD(date)} ${formatHour(hour)}`;

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** 同一天離 hour 最近的空檔；沒有空檔回 null。 */
function nearestFreeHour(activities: ActivityOut[], date: string, hour: number): number | null {
  const taken = new Set(activities.filter((a) => a.date === date).map((a) => a.hour));
  const free = HOURS.filter((h) => !taken.has(h));
  free.sort((a, b) => Math.abs(a - hour) - Math.abs(b - hour) || a - b);
  return free[0] ?? null;
}

function slotTakenMessage(
  activities: ActivityOut[],
  date: string,
  hour: number,
  occupiedBy: string | null | undefined,
): string {
  const occupant = activities.find((a) => a.id === occupiedBy);
  const free = nearestFreeHour(activities, date, hour);
  return (
    `SLOT_TAKEN：${when(date, hour)} 已有「${occupant?.title ?? "其他活動"}」（id ${occupiedBy}）。` +
    `要換掉請用 update_itinerary_activity 修改它，或改加在空檔${free === null ? "（這天沒有空檔了）" : ` ${formatHour(free)}`}。`
  );
}

/** 一天的概況，依回應長度由完整到精簡擇一（滿檔的一天有 16 個活動，每個 id 就 36 字元）。 */
function dayPlan(
  comparison: ComparisonOut,
  dates: string[],
  activities: ActivityOut[],
  date: string,
): object {
  const { stops, unmapped } = mapStops(dates, activities, "day", date);
  const numbers = new Map(stops.map((s) => [s.activity.id, s.n]));
  const ofDay = activities.filter((a) => a.date === date).sort((a, b) => a.hour - b.hour);
  const taken = new Set(ofDay.map((a) => a.hour));
  const head = {
    day: dates.indexOf(date) + 1,
    date,
    weekday: weekdayOf(date),
    city: comparison.city,
    free_hours: HOURS.filter((h) => !taken.has(h)),
    unmapped_count: unmapped,
  };
  const levels: object[] = [
    {
      ...head,
      activities: ofDay.map((a) => ({
        id: a.id,
        hour: a.hour,
        title: clip(a.title, 40),
        location: clip(a.location, 30),
        category: a.category,
        duration: a.duration,
        map_number: numbers.get(a.id) ?? null,
      })),
    },
    {
      ...head,
      truncated: true,
      activities: ofDay.map((a) => ({
        id: a.id,
        hour: a.hour,
        title: clip(a.title, 14),
        map_number: numbers.get(a.id) ?? null,
      })),
    },
    {
      ...head,
      truncated: true,
      columns: ["id", "hour", "title", "map_number"],
      rows: ofDay.map((a) => [a.id, a.hour, clip(a.title, 10), numbers.get(a.id) ?? null]),
    },
  ];
  return (
    levels.find((l) => JSON.stringify(l).length <= RESPONSE_LIMIT) ?? (levels.at(-1) as object)
  );
}

function tripState(ctx: WebMCPContext): object {
  const { comparison, activities, view } = ctx;
  if (!comparison) return { has_trip: false, hint: NO_TRIP };
  const dates = datesBetween(comparison.depart_date, comparison.return_date);
  return {
    has_trip: true,
    destination: comparison.destination,
    city: comparison.city,
    start_date: comparison.depart_date,
    end_date: comparison.return_date,
    day_count: dates.length,
    activity_count: activities.length,
    days: dates.map((date, i) => ({
      day: i + 1,
      date,
      activity_count: activities.filter((a) => a.date === date).length,
    })),
    selected_activity_id: view.selected?.id ?? null,
    map: { scope: view.scope, day: dates.indexOf(view.day) + 1 },
  };
}

type TripArgs = {
  destination: Destination;
  city?: string;
  start_date: string;
  end_date: string;
  confirm_remove?: boolean;
};
type ActivityArgs = DayRef & {
  hour: number;
  title: string;
  location?: string;
  description?: string;
  category?: ActivityCategory;
  duration?: number;
  latitude?: number;
  longitude?: number;
};
type UpdateArgs = Partial<ActivityArgs> & { activity_id: string };
type MapArgs = DayRef & { scope?: MapScope; activity_id?: string };

/**
 * 日程頁的 8 個 WebMCP 工具（ADR-0031）：離開頁面自動移除。工具與畫面按鈕共用同一組動作函式，
 * 等資料與畫面更新完才回傳；失敗時丟出可照著做的訊息。不呼叫 LLM。
 * 回傳是否支援 WebMCP（工具面板用）。
 */
export function useItineraryWebMCP(ctx: WebMCPContext): { supported: boolean } {
  const latest = useRef(ctx);
  latest.current = ctx;

  const trip = () => {
    const { comparison } = latest.current;
    if (!comparison) throw new Error(NO_TRIP);
    return { comparison, dates: datesBetween(comparison.depart_date, comparison.return_date) };
  };
  const findActivity = (id: string) => {
    const found = latest.current.activities.find((a) => a.id === id);
    if (!found) {
      throw new Error(`找不到這個活動（id ${id}），請用 get_day_plan 重新讀取當天日程。`);
    }
    return found;
  };

  const tripState_ = useWebMCP({
    name: "get_trip_state",
    ...TOOL_SCHEMAS.get_trip_state,
    execute: () => tripState(latest.current),
  });

  useWebMCP<DayRef>({
    name: "get_day_plan",
    ...TOOL_SCHEMAS.get_day_plan,
    execute: (args) => {
      const { comparison, dates } = trip();
      const { date } =
        args.day !== undefined || args.date !== undefined
          ? resolveDay(args, dates)
          : { date: latest.current.view.day };
      return dayPlan(comparison, dates, latest.current.activities, date);
    },
  });

  useWebMCP<TripArgs>({
    name: "set_trip_details",
    ...TOOL_SCHEMAS.set_trip_details,
    execute: async (args) => {
      if (!DESTINATIONS.includes(args.destination)) {
        throw new Error(
          `目的地要從這些選一個：${DESTINATIONS.join("、")}。城市請對應到所屬的國家或地區。`,
        );
      }
      const start = resolveTripDate(args.start_date);
      const end = resolveTripDate(args.end_date, start);
      const errors = validateTrip(start, end, latest.current.comparison?.depart_date);
      if (errors.length > 0) throw new Error(errors.join(" "));

      const before = latest.current;
      const oldDates = before.comparison
        ? datesBetween(before.comparison.depart_date, before.comparison.return_date)
        : [];
      const result = await before.actions.saveTrip(
        {
          destination: args.destination,
          city: (args.city ?? "").trim(),
          depart_date: start,
          return_date: end,
        },
        args.confirm_remove === true,
      );
      if (!result.ok) {
        if (result.code !== "WOULD_REMOVE_ACTIVITIES") throw new Error(result.message);
        const list = (
          result.detail as { activities: { date: string; hour: number; title: string }[] }
        ).activities;
        const names = list
          .slice(0, 8)
          .map((a) => `第 ${oldDates.indexOf(a.date) + 1} 天 ${formatHour(a.hour)}「${a.title}」`);
        throw new Error(
          `WOULD_REMOVE_ACTIVITIES：改成 ${formatMD(start)}–${formatMD(end)} 會移除 ${list.length} 個活動：${names.join("、")}${list.length > 8 ? " 等" : ""}。請先徵得使用者同意，再帶 confirm_remove: true 重新呼叫。`,
        );
      }
      const { comparison, removed } = result.data;
      const created = !before.comparison;
      if (created) {
        // 建立後網址換成日程頁、整頁重新掛載：等新頁面畫好
        await waitFor(() => document.querySelector('[data-testid="trip-banner"]'));
        latest.current.toast("agent 設定了旅行規劃");
      } else {
        before.syncForm(comparison);
        await waitFor(
          () =>
            latest.current.comparison?.depart_date === comparison.depart_date &&
            latest.current.comparison?.return_date === comparison.return_date &&
            latest.current.comparison?.city === comparison.city,
        );
        latest.current.toast(
          removed > 0 ? `agent 修改了日期，移除了 ${removed} 個活動` : "agent 修改了旅行規劃",
        );
      }
      return {
        created,
        destination: comparison.destination,
        city: comparison.city,
        start_date: comparison.depart_date,
        end_date: comparison.return_date,
        day_count: tripDays(comparison.depart_date, comparison.return_date),
        removed_activity_count: removed,
      };
    },
  });

  useWebMCP<{ activities: ActivityArgs[] }>({
    name: "add_itinerary_activities",
    ...TOOL_SCHEMAS.add_itinerary_activities,
    execute: async ({ activities: items }) => {
      const { dates } = trip();
      const problems: string[] = [];
      const payload: ActivityIn[] = [];
      items.forEach((item, i) => {
        try {
          const { date } = resolveDay(item, dates);
          if (!Number.isInteger(item.hour) || item.hour < FIRST_HOUR || item.hour > LAST_HOUR) {
            throw new Error(`時段只有 ${FIRST_HOUR}～${LAST_HOUR} 點（整點），收到 ${item.hour}。`);
          }
          if (!item.title?.trim()) throw new Error("缺少 title。");
          const { day: _day, date: _date, ...rest } = item;
          payload.push({ ...rest, date, title: item.title.trim() });
        } catch (e) {
          problems.push(`第 ${i + 1} 筆：${(e as Error).message}`);
        }
      });
      if (problems.length > 0) {
        throw new Error(
          `有 ${problems.length} 筆資料不能用，整批都沒有送出，請修正後重送：\n${problems.join("\n")}`,
        );
      }

      const result = await latest.current.actions.addActivities(payload);
      if (!result.ok) throw new Error(result.message);
      const { created, failed } = result.data;
      await waitFor(() =>
        created.every((c) => latest.current.activities.some((a) => a.id === c.id)),
      );
      const fresh = latest.current.activities;
      const failures = failed.map((f) => {
        const item = payload[f.index] as ActivityIn;
        return {
          index: f.index,
          code: f.code,
          message:
            f.code === "SLOT_TAKEN"
              ? slotTakenMessage(fresh, item.date, item.hour, f.occupied_by)
              : f.message,
          ...(f.occupied_by ? { occupied_by: f.occupied_by } : {}),
        };
      });
      if (created.length === 0) {
        throw new Error(
          `沒有新增任何活動：\n${failures.map((f) => `第 ${f.index + 1} 筆：${f.message}`).join("\n")}`,
        );
      }
      const only = created.length === 1 ? created[0] : undefined;
      if (only) {
        latest.current.view.setView({ activity: only });
        latest.current.toast(`agent 新增了 ${when(only.date, only.hour)}「${only.title}」`);
      } else {
        latest.current.toast(`agent 新增了 ${created.length} 個活動`);
      }
      const touched = [...new Set(created.map((c) => c.date))].sort();
      return {
        added_count: created.length,
        failed: failures,
        days: touched.map((date) => ({
          day: dates.indexOf(date) + 1,
          activity_count: fresh.filter((a) => a.date === date).length,
        })),
      };
    },
  });

  useWebMCP<UpdateArgs>({
    name: "update_itinerary_activity",
    ...TOOL_SCHEMAS.update_itinerary_activity,
    execute: async (args) => {
      const { dates } = trip();
      const target = findActivity(args.activity_id);
      const patch: ActivityPatch = {};
      const updated: string[] = [];
      if (args.day !== undefined || args.date !== undefined) {
        patch.date = resolveDay(args, dates).date;
        updated.push("date");
      }
      for (const key of [
        "hour",
        "title",
        "location",
        "description",
        "category",
        "duration",
        "latitude",
        "longitude",
      ] as const) {
        if (args[key] !== undefined) {
          Object.assign(patch, { [key]: args[key] });
          updated.push(key);
        }
      }
      if (updated.length === 0) {
        throw new Error(
          "沒有要修改的欄位：請至少給 day／date、hour、title、location、description、category、duration、latitude、longitude 其中之一。",
        );
      }
      const result = await latest.current.actions.changeActivity(target.id, patch);
      if (!result.ok) {
        if (result.code === "SLOT_TAKEN") {
          const occupied = (result.detail as { occupied_by?: string }).occupied_by;
          throw new Error(
            slotTakenMessage(
              latest.current.activities,
              patch.date ?? target.date,
              patch.hour ?? target.hour,
              occupied,
            ),
          );
        }
        throw new Error(result.message);
      }
      const saved = result.data;
      await waitFor(() =>
        latest.current.activities.some(
          (a) =>
            a.id === saved.id &&
            a.date === saved.date &&
            a.hour === saved.hour &&
            a.title === saved.title,
        ),
      );
      latest.current.view.setView({ activity: saved });
      latest.current.toast(`agent 修改了 ${when(saved.date, saved.hour)} 的活動`);
      return {
        id: saved.id,
        day: dates.indexOf(saved.date) + 1,
        hour: saved.hour,
        title: saved.title,
        updated_fields: updated,
      };
    },
  });

  useWebMCP<{ activity_id: string }>({
    name: "remove_itinerary_activity",
    ...TOOL_SCHEMAS.remove_itinerary_activity,
    execute: async ({ activity_id }) => {
      const { dates } = trip();
      const target = findActivity(activity_id);
      const result = await latest.current.actions.removeActivity(target.id);
      if (!result.ok) throw new Error(result.message);
      await waitFor(() => !latest.current.activities.some((a) => a.id === target.id));
      latest.current.toast(`agent 移除了 ${when(target.date, target.hour)}「${target.title}」`);
      return {
        removed_id: target.id,
        title: target.title,
        day: dates.indexOf(target.date) + 1,
        hour: target.hour,
      };
    },
  });

  useWebMCP<{ confirm?: boolean }>({
    name: "clear_itinerary_activities",
    ...TOOL_SCHEMAS.clear_itinerary_activities,
    execute: async ({ confirm }) => {
      trip();
      const count = latest.current.activities.length;
      if (count === 0) return { removed: 0 };
      if (confirm !== true) {
        throw new Error(
          `WOULD_REMOVE_ACTIVITIES：會移除全部 ${count} 個活動，什麼都還沒刪。請先徵得使用者同意，再帶 confirm: true 重新呼叫。`,
        );
      }
      const result = await latest.current.actions.clearActivities();
      if (!result.ok) throw new Error(result.message);
      await waitFor(() => latest.current.activities.length === 0);
      latest.current.toast(`agent 清除了所有活動（${result.data.removed} 個）`);
      return { removed: result.data.removed };
    },
  });

  useWebMCP<MapArgs>({
    name: "set_map_view",
    ...TOOL_SCHEMAS.set_map_view,
    execute: async (args) => {
      const { dates } = trip();
      const scope: MapScope = args.scope ?? "day";
      const target = args.activity_id ? findActivity(args.activity_id) : null;
      if (target && (target.latitude === null || target.longitude === null)) {
        throw new Error(
          "這個活動沒有座標，先用 update_itinerary_activity 補上 latitude 與 longitude。",
        );
      }
      const asked =
        args.day !== undefined || args.date !== undefined ? resolveDay(args, dates) : null;
      if (asked && target && asked.date !== target.date) {
        throw new Error(
          `這個活動在第 ${dates.indexOf(target.date) + 1} 天，不在第 ${asked.day} 天；請改 day 或換一個活動。`,
        );
      }
      latest.current.view.setView({ scope, date: asked?.date, activity: target ?? undefined });
      const wantDay = asked?.date ?? target?.date;
      await waitFor(
        () =>
          latest.current.view.scope === scope &&
          (!wantDay || latest.current.view.day === wantDay) &&
          (!target || latest.current.view.selected?.id === target.id),
      );
      const { view, activities } = latest.current;
      const { stops, unmapped } = mapStops(dates, activities, view.scope, view.day);
      return {
        scope: view.scope,
        day: dates.indexOf(view.day) + 1,
        selected_activity_id: view.selected?.id ?? null,
        stop_count: stops.length,
        unmapped_count: unmapped,
      };
    },
  });

  return { supported: tripState_.supported };
}
