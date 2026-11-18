import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import type { ActivityOut, Concern, Suggestion } from "../../client/types.gen";
import { SparkleIcon } from "../../components/icons";
import { ConcernIcon } from "./ConcernCard";

/**
 * 1-2 依日程推薦的時間軸（L1，掃描即 loading）：等 LLM 時列出整趟日程，光帶一天一天循環掃過，
 * 標題下輪播三個步驟的文案；結果回來時有風險的那天依序亮起、其餘天數收合，最後只留下有推薦的日子。
 */

const STEPS = ["讀取你的旅遊日程", "對照 9 項在意事項", "為每一項寫出理由"];
const STEP_MS = 1600;
// 這麼快就回來代表後端已經存過結果，不演動畫直接顯示
const FAST_MS = 600;
const ROW_MS = 480;
const SCAN_MS = 380;

export type Phase = "loading" | "revealing" | "done";

export type Row = { key: string; date: string | null; label: string; items: Suggestion[] };

const ISO = /\d{4}-\d{2}-\d{2}/;
// LLM 實際常寫 `12/19` 或 `12月19日`，不一定照 prompt 寫完整日期
const MONTH_DAY = /(\d{1,2})(?:\/|月)(\d{1,2})日?/;
const DAY_N = /^D(\d+)/;
const LEADING_DAY = /^(\d{4}-\d{2}-\d{2}|\d{1,2}(?:\/|月)\d{1,2}日?|D\d+)\s*/;

/**
 * 出發到回程的每一天；理由點名的日子（`2026-12-19`、`12/19`、`12月19日` 或 `D3`）掛上推薦，
 * 對不到日子的放最後的「整趟旅程」。
 */
export function suggestionRows(
  depart: string,
  returnDate: string,
  activities: ActivityOut[],
  suggestions: Suggestion[],
): Row[] {
  const days: string[] = [];
  // ponytail: 每天一列，長天數的行程會很長；超過兩週再考慮只列有活動的日子
  for (let d = new Date(`${depart}T00:00:00Z`); d.toISOString().slice(0, 10) <= returnDate; ) {
    days.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  const dayOf = (s: Suggestion) => {
    const iso = s.reason.match(ISO)?.[0];
    if (iso && days.includes(iso)) return iso;
    const [, month, date] = s.reason.match(MONTH_DAY) ?? [];
    const hit = days.find(
      (d) => +d.slice(5, 7) === Number(month) && +d.slice(8, 10) === Number(date),
    );
    if (hit) return hit;
    const n = s.reason.match(DAY_N)?.[1];
    return (n && days[Number(n) - 1]) || null;
  };
  const rows: Row[] = days.map((d) => ({
    key: d,
    date: d,
    label: activities
      .filter((a) => a.date === d)
      .sort((a, b) => a.hour - b.hour)
      .map((a) => a.title)
      .join("、"),
    items: suggestions.filter((s) => dayOf(s) === d),
  }));
  const rest = suggestions.filter((s) => dayOf(s) === null);
  if (rest.length > 0) rows.push({ key: "trip", date: null, label: "整趟旅程", items: rest });
  return rows;
}

/** 理由開頭的日期已經在日期框裡，不重複顯示。 */
const stripDay = (reason: string) => reason.replace(LEADING_DAY, "");

/** 每個推薦項目亮起的時間（毫秒），上方卡片的預先勾選跟著同一個節奏。 */
export function revealDelays(rows: Row[]): Map<Concern, number> {
  const delays = new Map<Concern, number>();
  rows
    .filter((r) => r.items.length > 0)
    .forEach((r, k) => {
      for (const s of r.items) delays.set(s.concern, k * ROW_MS + 150);
    });
  return delays;
}

const reducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/**
 * 動畫的進度。`settled`：推薦請求結束（成功或失敗）；`riskRows`：有推薦的列數，決定揭曉要多久。
 * `step` 是標題下輪播到第幾句（停在最後一句）；`scan` 是光帶目前在第幾列。
 */
export function useRevealPhase(settled: boolean, riskRows: number) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [step, setStep] = useState(0);
  const [scan, setScan] = useState(0);
  const start = useRef(performance.now());

  useEffect(() => {
    if (phase !== "loading") return;
    const t = setInterval(() => setStep((s) => (s < 2 ? s + 1 : s)), STEP_MS);
    return () => clearInterval(t);
  }, [phase]);

  useEffect(() => {
    if (phase !== "loading" || reducedMotion()) return;
    const t = setInterval(() => setScan((i) => i + 1), SCAN_MS);
    return () => clearInterval(t);
  }, [phase]);

  useEffect(() => {
    if (!settled) return;
    const elapsed = performance.now() - start.current;
    if (riskRows === 0 || elapsed < FAST_MS || reducedMotion()) {
      setPhase("done");
      return;
    }
    // 光帶停下、直接揭曉；等亮燈與收合演完再換成只留有推薦的日子
    setPhase("revealing");
    const t = setTimeout(() => setPhase("done"), riskRows * ROW_MS + 800);
    return () => clearTimeout(t);
  }, [settled, riskRows]);

  return { phase, step, scan };
}

const vars = (v: Record<string, string | number>) => v as CSSProperties;

function DateBox({ date, lit }: { date: string | null; lit: boolean }) {
  const d = date ? new Date(`${date}T00:00:00Z`) : null;
  return (
    <div
      // relative：時間軸的線是 absolute，日期框也要定位才會畫在線的上面
      className={`relative flex h-[52px] w-14 flex-col items-center justify-center rounded-btn border-2 bg-white ${lit ? "border-leaf text-heading" : "border-line text-muted"}`}
    >
      {d ? (
        <>
          <b className="t-caption-bold">{`${d.getUTCMonth() + 1}/${d.getUTCDate()}`}</b>
          <span className="t-micro font-normal text-muted">{`週${"日一二三四五六"[d.getUTCDay()]}`}</span>
        </>
      ) : (
        <b className="t-caption-bold">全程</b>
      )}
    </div>
  );
}

export function SuggestionTimeline({
  phase,
  step,
  scan,
  rows,
  selected,
  full,
  onToggle,
}: {
  phase: Phase;
  step: number;
  scan: number;
  rows: Row[];
  selected: Concern[];
  full: boolean;
  onToggle: (c: Concern) => void;
}) {
  const loading = phase === "loading";
  // 等待中只列日子（推薦可能已經回來、正在等動畫，「整趟旅程」先不出現）；結束後只留有推薦的
  const visible = rows.filter((r) =>
    loading ? r.date !== null : phase === "revealing" || r.items.length > 0,
  );
  const delays = revealDelays(rows);
  const collapseAt = delays.size === 0 ? 0 : Math.max(...delays.values()) + 250;
  const scanRow = loading ? scan % Math.max(visible.length, 1) : -1;
  let lit = -1;
  return (
    <section
      data-testid={loading ? "concern-suggestions-loading" : "concern-suggestions"}
      role={loading ? "status" : undefined}
      className="flex flex-col gap-4 rounded-tile bg-leaf-soft p-5"
    >
      {loading ? (
        <div className="flex items-center gap-2">
          <span className="size-5 shrink-0 animate-spin rounded-full border-2 border-leaf border-t-transparent motion-reduce:animate-none" />
          <div>
            <h3 className="m-0 t-label text-heading">{`正在看你 ${visible.length} 天的行程…`}</h3>
            <p className="m-0 t-caption text-muted">{STEPS[step]}</p>
          </div>
        </div>
      ) : (
        <div className="sg-fade-up flex items-center gap-2 text-leaf-text">
          <SparkleIcon className="size-5 flex-none" />
          <h3 className="m-0 t-label text-heading">我們從你的日程看到這些風險</h3>
        </div>
      )}
      <ol className="relative m-0 flex list-none flex-col p-0">
        <span
          aria-hidden="true"
          className="absolute top-[26px] bottom-[26px] left-[27px] w-0.5 bg-leaf/40"
        />
        {visible.map((r, i) => {
          const risk = !loading && r.items.length > 0;
          if (risk) lit += 1;
          const d = `${lit * ROW_MS}ms`;
          const collapse = phase === "revealing" && r.items.length === 0;
          return (
            <li
              key={r.key}
              className={`${loading ? "sg-day-in" : ""} ${collapse ? "sg-collapse" : ""}`}
              style={vars({ "--i": i, "--d": collapse ? `${collapseAt}ms` : d })}
            >
              <div className="min-h-0 overflow-hidden">
                <div
                  className={`grid grid-cols-[56px_1fr] gap-3 rounded-btn transition-colors motion-reduce:transition-none ${i < visible.length - 1 ? "pb-5" : ""} ${i === scanRow ? "bg-white/95" : ""}`}
                >
                  <div className={risk && phase === "revealing" ? "sg-reveal-date" : ""}>
                    <DateBox date={r.date} lit={risk || i === scanRow} />
                  </div>
                  <div className="flex min-w-0 flex-col justify-center gap-1.5">
                    <div
                      className={`t-label ${risk ? "text-ink" : "truncate text-muted"} ${i === scanRow ? "text-ink" : ""}`}
                    >
                      {r.label || "自由活動"}
                    </div>
                    {risk &&
                      r.items.map((s) => (
                        <div
                          key={s.concern}
                          className={`flex flex-col gap-1.5 ${phase === "revealing" ? "sg-reveal-in" : ""}`}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="inline-flex items-center gap-1.5 rounded-tag bg-white px-2.5 py-1 t-caption-bold text-brand">
                              →<ConcernIcon concern={s.concern} className="size-4 flex-none" />
                              {s.concern}
                            </span>
                            <PickButton
                              on={selected.includes(s.concern)}
                              disabled={!selected.includes(s.concern) && full}
                              onClick={() => onToggle(s.concern)}
                              concern={s.concern}
                            />
                          </div>
                          <p className="m-0 t-caption text-muted">{stripDay(s.reason)}</p>
                        </div>
                      ))}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function PickButton({
  on,
  disabled,
  onClick,
  concern,
}: {
  on: boolean;
  disabled: boolean;
  onClick: () => void;
  concern: Concern;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={on ? `取消${concern}` : `加入${concern}`}
      disabled={disabled}
      onClick={onClick}
      className="rounded-tag border-[1.5px] border-brand bg-white px-3 py-1 t-caption-bold text-brand aria-pressed:bg-brand aria-pressed:text-white disabled:cursor-not-allowed disabled:opacity-40"
    >
      {on ? "✓ 已選" : "加入"}
    </button>
  );
}
