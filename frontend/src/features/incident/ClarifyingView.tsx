import { Button } from "@base-ui/react/button";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { confirmIncident, sendIncidentMessage } from "../../client";
import { listIncidentsQueryKey } from "../../client/@tanstack/react-query.gen";
import type { IncidentContext } from "../../client/types.gen";
import { ActionBar } from "../../components/ui/ActionBar";
import { btn } from "../../components/ui/buttons";
import { LoadingOverlay } from "../../components/ui/LoadingOverlay";
import { PageHead } from "../../components/ui/PageHead";
import { SHORT_LANDSCAPE_QUERY, useMediaQuery } from "../../hooks/useMediaQuery";
import { ConfirmDialog } from "./ConfirmDialog";
import { type ChatLine, DescribeChat, FIRST_QUESTION } from "./DescribeChat";
import { errorText, policyMeta, SCROLL_TO_TODOS_STATE, tripTitle, useIncident } from "./shared";

/** 2-2g-3 按「還要補充或修改」後對話裡顯示的固定句（Leon 的模擬畫面）；前端顯示，不送給 5 號、不存。 */
export const SUPPLEMENT_PROMPT = "好的，請說明要補充或修改的地方。";

/**
 * 2-2 描述事故（追問中）：事件已建立、還沒確認時的畫面（5 號還在追問，或問完等使用者確認）。
 * 5 號問完而且有變就跳 2-2g 現況確認彈窗；按「確認」才進行程頁（#345）。
 * 對話從 `ctx.messages` 來，重新整理後選項與出處都還在（#212）。
 * `confirmation` 是進來時事件的狀態：`awaiting`（上次問完沒確認就離開）一進來就再跳彈窗。
 */
export function ClarifyingView({
  id,
  ctx,
  confirmation: initialConfirmation = "clarifying",
  focusAsk = false,
}: {
  id: string;
  ctx: IncidentContext;
  confirmation?: string;
  /** 從建立事故頁送出描述後進來（路由 state，#441）：輸入框自動對焦，旅客可以直接答第一題。 */
  focusAsk?: boolean;
}) {
  const { refresh } = useIncident(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [confirmation, setConfirmation] = useState(initialConfirmation);
  const [dialogOpen, setDialogOpen] = useState(initialConfirmation === "awaiting");
  const [confirming, setConfirming] = useState(false);
  // 一進來就跳確認彈窗（5 號一輪就問完）時不對焦：焦點留給彈窗，不搶到它後面的輸入框
  const [focusInput] = useState(focusAsk && initialConfirmation !== "awaiting");
  const [confirmError, setConfirmError] = useState<string | null>(null);
  // 按「還要補充或修改」後顯示的固定句；下一次送出就換成真正的對話
  const [supplementPrompt, setSupplementPrompt] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  // 剛送出、回覆還沒回來的那句話：先顯示在對話最下面，回覆回來換成真正的訊息
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 橫式矮視窗、鍵盤開著時，底部的動作列會被鍵盤蓋住：「回到確認」搬到輸入列旁邊（#409）
  const shortLandscape = useMediaQuery(SHORT_LANDSCAPE_QUERY);

  const messages = ctx.messages ?? [];
  const last = messages.at(-1);
  // 快速選項只在最後一則是 5 號的訊息時顯示：旅客已經回答過的舊問題，選項不再出現
  const quickReplies = last?.role === "assistant" ? (last.quick_replies ?? []) : [];
  const lines: ChatLine[] = [
    { from: "ai", text: FIRST_QUESTION },
    ...messages.map((m) => ({
      from: m.role === "user" ? ("me" as const) : ("ai" as const),
      text: m.content,
      citations: m.citations,
    })),
    ...(pending && !(last?.role === "user" && last.content === pending)
      ? [{ from: "me" as const, text: pending }]
      : []),
    ...(supplementPrompt && !busy ? [{ from: "ai" as const, text: SUPPLEMENT_PROMPT }] : []),
  ];

  async function send(content: string, mode: "text" | "quick_reply") {
    if (!content.trim() || busy) return;
    setBusy(true);
    setError(null);
    setPending(content);
    setSupplementPrompt(false);
    const res = await sendIncidentMessage({
      path: { incident_id: id },
      body: { content, input_mode: mode },
    });
    if (!res.data) {
      setPending(null);
      setBusy(false);
      return setError(errorText(res.error, res.response?.status));
    }
    // 回應裡的事件是送出當下的快照，不能直接蓋掉快取，改成重新讀取（5 號回覆可能很慢）
    await refresh();
    // 事件的確認狀態變了（例如問完、或補充後退回追問）：首頁行程列與進來時讀的狀態要跟著更新
    await queryClient.invalidateQueries({ queryKey: listIncidentsQueryKey() });
    setConfirmation(res.data.confirmation);
    // 5 號問完而且這輪有改到狀況才跳；等確認時只是問問題不跳（對話裡有「回到確認」）
    if (res.data.show_confirm) setDialogOpen(true);
    setText("");
    setPending(null);
    setBusy(false);
  }

  async function confirm() {
    if (confirming) return;
    setConfirming(true);
    setConfirmError(null);
    const res = await confirmIncident({ path: { incident_id: id } });
    if (!res.data) {
      setConfirming(false);
      return setConfirmError(errorText(res.error, res.response?.status));
    }
    // 事件進了行程：行程列表、行程頁、事件清單都要重新讀
    await queryClient.invalidateQueries();
    await navigate({
      to: "/trip/$policyId",
      params: { policyId: res.data.policy_id },
      search: { incident: id },
      replace: true,
      // 有待辦就捲到理賠待辦（#449）
      state: (prev) => ({ ...prev, ...SCROLL_TO_TODOS_STATE }),
    });
  }

  const backToConfirm = confirmation === "awaiting" && !dialogOpen && (
    <Button className={btn} onClick={() => setDialogOpen(true)} data-testid="back-to-confirm">
      回到確認
    </Button>
  );

  return (
    <main data-page-paper className="mx-auto flex max-w-[1040px] flex-col gap-6 font-ui md:px-6">
      <PageHead
        crumb="出事了：幫我處理理賠 · 描述事故"
        title={tripTitle(ctx.destination, ctx.policy.start_date, ctx.policy.end_date)}
        meta={policyMeta(ctx.policy.plan_name, ctx.policy.sum_insured)}
      />
      {error && (
        <p
          role="alert"
          className="m-0 rounded-btn bg-danger-soft px-4 py-2 t-caption-bold text-danger-ink"
          data-testid="error"
        >
          {error}
        </p>
      )}

      <DescribeChat
        lines={lines}
        quickReplies={quickReplies}
        placeholder={quickReplies.length > 0 ? "回答上面的問題，或點選項" : "回答上面的問題"}
        value={text}
        onChange={setText}
        busy={busy}
        typingLabel="正在整理您的回答"
        onSend={(content, mode) => void send(content, mode)}
        askLeading={shortLandscape ? backToConfirm : undefined}
        focusInput={focusInput}
      />

      <ConfirmDialog
        open={dialogOpen}
        ctx={ctx}
        busy={confirming}
        error={confirmError}
        onSupplement={() => {
          setDialogOpen(false);
          setSupplementPrompt(true);
        }}
        onConfirm={() => void confirm()}
      />

      {/* 按確認後到行程頁之間：遮住畫面，行程頁讀好才拿掉（#445）；成功後會換頁，所以不用再關 */}
      {confirming && <LoadingOverlay>正在整理您的行程…</LoadingOverlay>}

      <ActionBar>
        {!shortLandscape && backToConfirm}
        <ActionBar.Push>
          <Link to="/" className={btn}>
            回首頁
          </Link>
        </ActionBar.Push>
      </ActionBar>
    </main>
  );
}
