import { useEffect, useRef } from "react";
import type { ReminderRound } from "../incident/ReminderProgress";

/** 過了預計時間後多久重新讀取一次：信在下一次掃描時寄出（測試設定 15 秒，正式環境 10 分鐘） */
export const REMINDER_POLL_MS = 15_000;
/** 離預計時間還很久時，最多等這麼久就重新讀取一次（電腦睡眠、時鐘調整後也能跟上） */
const MAX_WAIT_MS = 10 * 60 * 1000;

/**
 * 下一次重新讀取行程要等多久；目前這一輪沒有還沒寄的信（已結束、寄滿、還沒開始計時）時不讀，回 null。
 * 離下一封的預計時間還久就等到那時候；已經過了就每 `REMINDER_POLL_MS` 讀一次，直到寄出。
 */
export function reminderPollDelay(rounds: ReminderRound[], now: number): number | null {
  const current = rounds.at(-1);
  if (!current || current.ended_at) return null;
  const next = current.letters.find((l) => !l.sent_at);
  if (!next) return null;
  const wait = new Date(next.planned_at).getTime() - now;
  return wait > REMINDER_POLL_MS ? Math.min(wait, MAX_WAIT_MS) : REMINDER_POLL_MS;
}

/**
 * 提醒信寄出後，行程頁不用重新整理就看得到（ADR-0031）：這一輪還有信沒寄時，依預計時間自動重新讀取行程。
 * 分頁在背景時不讀，切回來時馬上讀一次。`refresh` 是 `useTrip` 的重新讀取。
 */
export function useReminderPolling(
  rounds: ReminderRound[],
  enabled: boolean,
  refresh: () => Promise<unknown>,
) {
  // refresh 每次 render 都是新的函式：放進 ref，免得每次 render 都重排計時
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  useEffect(() => {
    if (!enabled || reminderPollDelay(rounds, Date.now()) === null) return;
    let cancelled = false;
    let timer: number | undefined;
    // 資料沒變時（還沒寄出）effect 不會重跑，所以讀完自己排下一次；資料變了 effect 重跑，舊的這一串停掉
    const schedule = () => {
      const delay = reminderPollDelay(rounds, Date.now());
      if (cancelled || delay === null) return;
      timer = window.setTimeout(async () => {
        if (!document.hidden) await refreshRef.current();
        schedule();
      }, delay);
    };
    const onVisible = () => {
      if (!document.hidden) void refreshRef.current();
    };
    schedule();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [rounds, enabled]);
}
