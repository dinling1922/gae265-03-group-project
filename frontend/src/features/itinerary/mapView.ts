import { useState } from "react";
import type { ActivityOut } from "../../client/types.gen";

export type MapScope = "day" | "trip";

/** 地圖上的一站：有座標的活動，加上編號（單日是 1、2、3，整趟是 第幾天-第幾個）。 */
export type Stop = { activity: ActivityOut; n: string; dayIndex: number };

/** 單日或整趟要畫在地圖上的站（依日期、時段排序），以及範圍內沒有座標的活動數。 */
export function mapStops(
  dates: string[],
  activities: ActivityOut[],
  scope: MapScope,
  day: string,
): { stops: Stop[]; unmapped: number } {
  const shown = scope === "day" ? dates.filter((d) => d === day) : dates;
  const stops: Stop[] = [];
  let unmapped = 0;
  for (const date of shown) {
    const dayIndex = dates.indexOf(date);
    const ofDay = activities.filter((a) => a.date === date).sort((a, b) => a.hour - b.hour);
    let k = 0;
    for (const activity of ofDay) {
      if (activity.latitude === null || activity.longitude === null) {
        unmapped += 1;
        continue;
      }
      k += 1;
      stops.push({ activity, n: scope === "day" ? String(k) : `${dayIndex + 1}-${k}`, dayIndex });
    }
  }
  return { stops, unmapped };
}

/**
 * 時間軸、地圖、地點清單共用的畫面狀態：目前看哪一天、單日或整趟、選取哪個活動。
 * 不存資料庫。`setView` 讓程式（WebMCP 的 set_map_view）一次設定；選到別天的活動時日期跟著切換。
 */
export function useMapView(dates: string[], activities: ActivityOut[]) {
  const [activeDate, setActiveDate] = useState(dates[0] ?? "");
  const [scope, setScope] = useState<MapScope>("day");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // 日期被縮短、活動被刪掉之後，目前的日期與選取可能不存在，退回第一天與不選
  const day = dates.includes(activeDate) ? activeDate : (dates[0] ?? "");
  const selected = activities.find((a) => a.id === selectedId) ?? null;

  return {
    scope,
    day,
    selected,
    /** 點日期分頁或地圖的「第 N 天」：看那一天的單日地圖。 */
    showDay: (date: string) => {
      setActiveDate(date);
      setScope("day");
    },
    showTrip: () => setScope("trip"),
    /** 選取活動；整趟地圖時留在整趟，只是日期分頁跟著切到那一天。 */
    select: (activity: ActivityOut) => {
      setSelectedId(activity.id);
      setActiveDate(activity.date);
    },
    /** 一次設定（WebMCP）：沒給的欄位維持原樣；activity 給了就選取並切到它的日期。 */
    setView: (view: { scope?: MapScope; date?: string; activity?: ActivityOut }) => {
      if (view.scope) setScope(view.scope);
      if (view.date) setActiveDate(view.date);
      if (view.activity) {
        setSelectedId(view.activity.id);
        setActiveDate(view.activity.date);
      }
    },
  };
}
