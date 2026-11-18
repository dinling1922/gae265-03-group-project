import type { Destination } from "../../client/types.gen";

// 原型順序；satisfies 讓產生的 Destination 型別多或少一個時編譯失敗
export const DESTINATIONS = Object.keys({
  美國: 0,
  加拿大: 0,
  日本: 0,
  韓國: 0,
  新加坡: 0,
  英國: 0,
  歐洲申根國家: 0,
  澳洲: 0,
  紐西蘭: 0,
  中國大陸: 0,
  香港澳門: 0,
  泰國: 0,
  越南: 0,
  馬來西亞: 0,
  菲律賓: 0,
  印尼: 0,
} satisfies Record<Destination, 0>) as Destination[];

/** 日程的時段範圍：7:00～22:00，一小時一格（與後端 app.domain.itinerary 一致）。 */
export const FIRST_HOUR = 7;
export const LAST_HOUR = 22;
export const HOURS = Array.from({ length: LAST_HOUR - FIRST_HOUR + 1 }, (_, i) => FIRST_HOUR + i);

/** 瀏覽器本地日期的 YYYY-MM-DD，可以直接跟 `<input type="date">` 的值比大小。 */
export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 含頭尾的天數；兩個日期都當 UTC 午夜解析，相減不受夏令時間影響。 */
export function tripDays(depart: string, returnDate: string): number {
  return (Date.parse(returnDate) - Date.parse(depart)) / 86_400_000 + 1;
}

/** 日期字串加 n 天（UTC 計算，同 tripDays）。 */
export function addDays(date: string, n: number): string {
  return new Date(Date.parse(date) + n * 86_400_000).toISOString().slice(0, 10);
}

/** 出發日到回程日的每一天（YYYY-MM-DD）。 */
export function datesBetween(depart: string, returnDate: string): string[] {
  return Array.from({ length: tripDays(depart, returnDate) }, (_, i) => addDays(depart, i));
}

const WEEKDAY = ["日", "一", "二", "三", "四", "五", "六"];

/** 「五」；星期用 UTC 算，與日期字串一致。 */
export function weekdayOf(date: string): string {
  return WEEKDAY[new Date(Date.parse(date)).getUTCDay()] ?? "";
}

/** 「9/18（五）」；星期用 UTC 算，與日期字串一致。 */
export function formatDay(date: string): string {
  const d = new Date(Date.parse(date));
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}（${WEEKDAY[d.getUTCDay()]}）`;
}

/** 「9/18」。 */
export function formatMD(date: string): string {
  const d = new Date(Date.parse(date));
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
}

export function formatHour(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

/** 與後端 ComparisonIn 同一套規則與訊息（spec #75）。出發日可以是過去。 */
export function validateTrip(depart: string, returnDate: string): string[] {
  const errors: string[] = [];
  if (depart && returnDate) {
    if (returnDate < depart) errors.push("回程日不能早於出發日。");
    else if (tripDays(depart, returnDate) < 4) errors.push("旅遊天數最短 4 天。");
    else if (tripDays(depart, returnDate) > 10) errors.push("旅遊天數最長 10 天。");
  }
  return errors;
}
