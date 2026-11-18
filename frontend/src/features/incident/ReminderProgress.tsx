import type { ReactNode } from "react";
import type { ReminderRoundOut, ReminderTriggerOut } from "../../client/types.gen";
import { ClockIcon, MailIcon, MailOffIcon, MailSentIcon } from "../../components/icons";

/**
 * 提醒信寄送進度（ADR-0031）：每一輪一條「起點 → 第 1、2、3 封」的條狀進度。
 * 第一輪（畫面上叫「初次理賠待辦」）從開始缺文件起算，第一封寄出前新增的事故或狀況都算在這一輪；
 * 寄過信之後才有新增狀況（新的需上傳待辦），就重新開始一輪，上一輪還沒寄的信不再寄。
 * 已寄出的封顯示實際寄出時間（和信件的 Date 標頭相同，可以對照 Gmail），還沒寄的顯示預計時間。
 *
 * 樣式沿用上傳進度的步驟圓點（UploadProcessingRow 的 StepNote）：已寄出是綠底白色「信封＋勾」，
 * 下一封是綠框信封，還沒輪到是灰框信封，不再寄是虛線框「信封＋叉」；起點是時鐘（開始計時）。
 *
 * 資料來自行程頁 API 的 `TripOut.reminder_rounds`（後端 `app/domain/reminder/rounds.py`）。
 */

/** 一輪提醒信；時間都是 ISO 字串。 */
/** 開始這一輪的事故（`new_incident` 新增了這個事故，`new_situation` 原本的事故有新狀況）。 */
export type ReminderTrigger = ReminderTriggerOut;

/**
 * 一輪提醒信（行程頁 API 的 `TripOut.reminder_rounds`）：起點、被下一輪取代的時間（目前這一輪是 null）、
 * 因為哪個事故開始（第一輪是 null）、固定 3 封的預計與實際寄出時間。
 */
export type ReminderRound = ReminderRoundOut;

const TAIPEI = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** 台灣時間「11/17 11:37」。 */
export function reminderTime(iso: string): string {
  const parts = Object.fromEntries(
    TAIPEI.formatToParts(new Date(iso)).map((p) => [p.type, p.value]),
  );
  return `${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`;
}

/** 台灣時間的「11/16」：輪次標籤用。 */
export function reminderDay(iso: string): string {
  return reminderTime(iso).split(" ")[0] ?? "";
}

type StepState = "ok" | "next" | "wait" | "skip";

function stepState(round: ReminderRound, index: number): StepState {
  const letter = round.letters[index];
  if (letter?.sent_at) return "ok";
  if (round.ended_at) return "skip";
  const firstUnsent = round.letters.findIndex((l) => !l.sent_at);
  return firstUnsent === index ? "next" : "wait";
}

/** 一句話的現況：放在進度條下方，也給螢幕閱讀器讀。 */
/** 新的一輪因為什麼開始：新增的事故，或哪個事故有新狀況；不知道時寫「新增狀況」。 */
export function triggerText(trigger: ReminderTrigger | null): string {
  if (!trigger) return "新增狀況";
  return trigger.kind === "new_incident"
    ? `新增事故「${trigger.incident}」`
    : `「${trigger.incident}」新增狀況`;
}

/** 這一輪為什麼結束；`endedBy` 是取代這一輪的下一輪的 `trigger`（`new_round` 時用）。 */
function endText(round: ReminderRound, endedBy: ReminderTrigger | null): string {
  if (round.end_reason === "completed") return "需上傳的文件都齊全了";
  if (round.end_reason === "closed") return "行程已結案";
  return triggerText(endedBy);
}

/** `endedBy` 是取代這一輪的下一輪的 `trigger`。 */
export function reminderSummary(
  round: ReminderRound,
  endedBy: ReminderTrigger | null = null,
): string {
  const sent = round.letters.filter((l) => l.sent_at).length;
  const total = round.letters.length;
  if (round.ended_at) {
    const reason = `${reminderDay(round.ended_at)} ${endText(round, endedBy)}`;
    const replaced = round.end_reason === "new_round";
    if (sent === total) {
      return replaced ? `這一輪 ${total} 封都已寄出。` : `${reason}，這一輪 ${total} 封都已寄出。`;
    }
    const skipped = sent + 1 === total ? `第 ${total} 封` : `第 ${sent + 1}～${total} 封`;
    return replaced
      ? `${reason}，${skipped}不再寄，改寄新一輪的提醒信。`
      : `${reason}，${skipped}不再寄。`;
  }
  const next = round.letters.find((l) => !l.sent_at);
  if (!next) return `這一輪 ${total} 封都已寄出。之後如果有新增狀況，會重新提醒。`;
  if (sent === 0) return `第 1 封預計 ${reminderTime(next.planned_at)} 寄出。`;
  return `已寄出第 ${sent} 封，下一封預計 ${reminderTime(next.planned_at)} 寄出。`;
}

const DOT: Record<StepState, string> = {
  ok: "border-solid border-leaf bg-leaf text-white",
  next: "border-solid border-leaf bg-white text-leaf-text",
  wait: "border-solid border-line bg-paper text-muted",
  skip: "border-dashed border-line bg-white text-muted",
};

const STATUS: Record<StepState, string> = {
  ok: "已寄出",
  next: "預計",
  wait: "預計",
  skip: "不再寄",
};

function Step({
  name,
  status,
  time,
  state,
  icon,
}: {
  name: string;
  status: string;
  time: string | null;
  state: StepState;
  icon: ReactNode;
}) {
  return (
    <li
      className="relative z-10 flex min-w-0 flex-col items-center text-center"
      aria-current={state === "next" ? "step" : undefined}
      data-testid="reminder-step"
      data-state={state}
    >
      <span
        aria-hidden="true"
        className={`inline-grid size-[34px] place-items-center rounded-full border-2 ${DOT[state]}`}
      >
        {icon}
      </span>
      <span
        className={`mt-1.5 whitespace-nowrap text-[13px] leading-5 font-bold md:t-caption-bold ${
          state === "wait" || state === "skip" ? "text-muted" : "text-heading"
        }`}
      >
        {name}
      </span>
      <span
        className={`text-[13px] leading-5 ${state === "next" ? "font-bold text-leaf-text" : "text-muted"}`}
      >
        <span className="block">{status}</span>
        {/* 手機上日期與時間分兩行，避免一欄裡斷在奇怪的地方 */}
        {time?.split(" ").map((part) => (
          <span key={part} className="block md:ml-1 md:inline">
            {part}
          </span>
        ))}
      </span>
    </li>
  );
}

const ICON: Record<StepState, ReactNode> = {
  ok: <MailSentIcon className="size-[18px]" />,
  next: <MailIcon className="size-[18px]" />,
  wait: <MailIcon className="size-[18px]" />,
  skip: <MailOffIcon className="size-[18px]" />,
};

/**
 * 一輪的條狀進度；`first` 是第一輪（起點是開始缺文件），其他輪的起點是新增的事故或狀況。
 * `endedBy` 是取代這一輪的下一輪的 `trigger`，用在「不再寄」的說明。
 */
export function ReminderProgress({
  round,
  first,
  endedBy = null,
}: {
  round: ReminderRound;
  first: boolean;
  endedBy?: ReminderTrigger | null;
}) {
  const sent = round.letters.filter((l) => l.sent_at).length;
  return (
    <div className="flex flex-col gap-3" data-testid="reminder-progress">
      <div className="relative">
        {/* 連接線：從第一個圓圈中心到最後一個；寄出幾封，綠線就延伸到第幾封 */}
        <span
          aria-hidden="true"
          className="absolute top-4 right-[12.5%] left-[12.5%] h-0.5 bg-line"
        />
        <span
          aria-hidden="true"
          className="absolute top-4 left-[12.5%] h-0.5 bg-leaf"
          style={{ width: `${(75 * sent) / round.letters.length}%` }}
        />
        <ol aria-label="寄送進度" className="m-0 grid list-none grid-cols-4 p-0">
          <Step
            name={
              first ? "缺文件" : round.trigger?.kind === "new_incident" ? "新增事故" : "新增狀況"
            }
            status="起算"
            time={reminderTime(round.started_at)}
            state="ok"
            icon={<ClockIcon className="size-[18px]" />}
          />
          {round.letters.map((letter, i) => {
            const state = stepState(round, i);
            return (
              <Step
                // biome-ignore lint/suspicious/noArrayIndexKey: 固定 3 封，順序不變
                key={i}
                name={`第 ${i + 1} 封`}
                status={STATUS[state]}
                time={state === "skip" ? null : reminderTime(letter.sent_at ?? letter.planned_at)}
                state={state}
                icon={ICON[state]}
              />
            );
          })}
        </ol>
      </div>
      <p
        className="m-0 t-caption text-muted"
        role={round.ended_at ? undefined : "status"}
        data-testid="reminder-summary"
      >
        {reminderSummary(round, endedBy)}
      </p>
    </div>
  );
}
