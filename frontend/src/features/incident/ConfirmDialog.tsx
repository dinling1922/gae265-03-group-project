import { Button } from "@base-ui/react/button";
import type { ComponentProps } from "react";
import type { IncidentContext } from "../../client/types.gen";
import { btn, btnPri } from "../../components/ui/buttons";
import { DialogShell } from "../../components/ui/DialogShell";
import { dateOf, IncidentCardBody } from "./IncidentCard";

/** 2-2g-1 說明：有待辦與只有待確認事項、沒有待辦兩種（規格 2-2g-1）。 */
const DESC_WITH_TODOS = "請確認以下內容是否正確。確認後會產生理賠待辦，並進入待辦事項。";
/** 行程頁問答補完資訊、結論或待辦有變時再跳（#445）：事件早就確認過、待辦也已經更新。 */
const DESC_UPDATED = "您補充的內容讓判斷有變，請確認以下內容是否正確。待辦已依最新結果更新。";
const DESC_NO_TODOS =
  "目前還有事項需要確認，暫時不會產生待辦。可以先補充，或進入行程頁後在問答裡補充。";

/**
 * 2-2g 現況確認彈窗（規格 v3.10）：標題「確認目前的狀況」；沒有關閉鈕，點外面或按 Esc 都不會關，
 * 一定要選「還要補充或修改」或「確認，進入待辦事項」（沒有待辦時按鈕文字是「確認，進入行程頁」）。
 * 事故總結與 2-3a 共用同一個 `IncidentCardBody`。
 */
export function ConfirmDialog({
  open,
  ctx,
  busy,
  error,
  onSupplement,
  onConfirm,
  mode = "first",
  finalFocus,
}: {
  open: boolean;
  ctx: IncidentContext;
  busy: boolean;
  error: string | null;
  onSupplement: () => void;
  onConfirm: () => void;
  /**
   * `first`（預設）：第一次描述問完，按確認才進行程頁。
   * `update`：行程頁問答補完資訊後再確認，按確認只關彈窗，文字不提「進入」。
   */
  mode?: "first" | "update";
  finalFocus?: ComponentProps<typeof DialogShell>["finalFocus"];
}) {
  // ctx.covered：至少一項保障可能適用才會有待辦；其他（資訊不足、全部不在保障範圍）沒有待辦
  const hasTodos = ctx.covered !== false;
  const date = dateOf(ctx);
  const name = `${ctx.incident_type_name ?? "事故"}${date ? ` · ${date}` : ""}`;
  const unsure = (ctx.clauses ?? []).filter((c) => c.conclusion === "insufficient").length;
  return (
    <DialogShell
      open={open}
      // 沒有關閉鈕：點外面、按 Esc 都不關
      onOpenChange={() => {}}
      variant="wide"
      testId="confirm-dialog"
      title="確認目前的狀況"
      finalFocus={finalFocus}
      description={
        <p className="m-0">
          {mode === "update" ? DESC_UPDATED : hasTodos ? DESC_WITH_TODOS : DESC_NO_TODOS}
        </p>
      }
      actions={
        <>
          <Button
            className={btn}
            onClick={onSupplement}
            disabled={busy}
            data-testid="confirm-supplement"
          >
            還要補充或修改
          </Button>
          <Button className={btnPri} onClick={onConfirm} disabled={busy} data-testid="confirm-yes">
            {mode === "update" ? "確認" : hasTodos ? "確認，進入待辦事項" : "確認，進入行程頁"}
          </Button>
        </>
      }
    >
      <section aria-label={`事故 ${name}`} className="flex flex-col gap-3.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h3 className="m-0 t-subsection-title text-heading" data-testid="confirm-title">
            {name}
          </h3>
          {unsure > 0 && (
            <span
              data-testid="confirm-unsure"
              className="rounded-xl bg-warn-soft px-2.5 py-0.5 text-[13px] leading-5 font-bold text-warn-ink"
            >
              待確認 {unsure}
            </span>
          )}
        </div>
        <IncidentCardBody ctx={ctx} id="confirm-body" />
      </section>
      {error && (
        <p role="alert" className="m-0 t-caption-bold text-danger" data-testid="confirm-error">
          {error}
        </p>
      )}
    </DialogShell>
  );
}
