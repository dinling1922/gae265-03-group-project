import { formatMD, today } from "./tripRules";

/** 日期解析失敗：訊息帶 INVALID_DATE，agent 看得懂該怎麼辦。 */
export class InvalidDate extends Error {
  constructor(input: string, hint = "請用 YYYY-MM-DD 或 M/D（例如 9/18），或請使用者說出確切日期") {
    super(`INVALID_DATE：無法解析「${input}」。${hint}。`);
  }
}

const ISO = /^(\d{4})-(\d{1,2})-(\d{1,2})$/;
const MD = /^(\d{1,2})\/(\d{1,2})$/;

function isoOf(year: number, month: number, day: number): string | null {
  const d = new Date(Date.UTC(year, month - 1, day));
  // 2/30 之類的日期 Date 會自動進位，轉回來對不上就是不存在
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) {
    return null;
  }
  return d.toISOString().slice(0, 10);
}

/** `YYYY-MM-DD` 回完整日期；`M/D` 回月與日（年份由呼叫端決定）；其他格式 null。 */
export function parseDateInput(
  input: string,
): { iso: string } | { month: number; day: number } | null {
  const text = input.trim();
  const iso = ISO.exec(text);
  if (iso) {
    const full = isoOf(Number(iso[1]), Number(iso[2]), Number(iso[3]));
    return full ? { iso: full } : null;
  }
  const md = MD.exec(text);
  if (md && isoOf(2000, Number(md[1]), Number(md[2]))) {
    return { month: Number(md[1]), day: Number(md[2]) };
  }
  return null;
}

/** 從 `from` 那天（含）起，第一個月日相符的日期；年份不用模型推算（agent 只給 M/D）。 */
export function nextOccurrence(month: number, day: number, from: string): string | null {
  const year = Number(from.slice(0, 4));
  for (const y of [year, year + 1, year + 2, year + 3]) {
    const iso = isoOf(y, month, day);
    if (iso && iso >= from) return iso;
  }
  return null;
}

/** set_trip_details 的日期：完整日期照用；M/D 的出發日取今年的那天（可以是過去），回程日取出發日起的下一個。 */
export function resolveTripDate(
  input: string,
  notBefore: string = `${today().slice(0, 4)}-01-01`,
): string {
  const parsed = parseDateInput(input);
  if (!parsed) throw new InvalidDate(input);
  if ("iso" in parsed) return parsed.iso;
  const found = nextOccurrence(parsed.month, parsed.day, notBefore);
  if (!found) throw new InvalidDate(input);
  return found;
}

export type DayRef = { day?: number; date?: string };

/**
 * 指定日子的參數（day 第幾天，或 date）換成旅行日期之一。
 * 日子由程式換算，不讓模型推算；不在這趟旅行之內時，錯誤寫明範圍。
 */
export function resolveDay(ref: DayRef, dates: string[]): { date: string; day: number } {
  const range = `${formatMD(dates[0] ?? "")}–${formatMD(dates[dates.length - 1] ?? "")}`;
  if (ref.day !== undefined) {
    if (!Number.isInteger(ref.day) || ref.day < 1 || ref.day > dates.length) {
      throw new Error(
        `這趟只有 ${dates.length} 天（第 1～${dates.length} 天），沒有第 ${ref.day} 天。`,
      );
    }
    const date = dates[ref.day - 1] as string;
    if (ref.date !== undefined) {
      const also = resolveDay({ date: ref.date }, dates);
      if (also.date !== date) {
        throw new Error(
          `day ${ref.day}（${formatMD(date)}）與 date ${ref.date} 指的不是同一天，請只給其中一個。`,
        );
      }
    }
    return { date, day: ref.day };
  }
  if (ref.date === undefined)
    throw new Error("請用 day（第幾天）或 date（YYYY-MM-DD 或 M/D）指定日子。");
  const parsed = parseDateInput(ref.date);
  if (!parsed) throw new InvalidDate(ref.date);
  const date =
    "iso" in parsed
      ? parsed.iso
      : dates.find(
          (d) =>
            d.slice(5) ===
            `${String(parsed.month).padStart(2, "0")}-${String(parsed.day).padStart(2, "0")}`,
        );
  if (!date || !dates.includes(date)) {
    throw new Error(
      `${ref.date} 不在這趟旅行（${range}）之內，請改用 ${range} 之間的日期，或用 day 指定第幾天。`,
    );
  }
  return { date, day: dates.indexOf(date) + 1 };
}
