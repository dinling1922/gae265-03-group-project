import { Button } from "@base-ui/react/button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { createLineBindCode, deleteLineBinding, getLineBinding } from "../../client";
import { getLineBindingQueryKey } from "../../client/@tanstack/react-query.gen";
import type { LineBindingStatusOut } from "../../client/types.gen";
import { MailIcon } from "../../components/icons";
import { btnSm, btnSmDanger, btnSmPri } from "../../components/ui/buttons";
import { Pill } from "../../components/ui/Tag";

/** 等待綁定完成時，多久問一次狀態（毫秒）。分頁在背景時 TanStack Query 會自動暫停。 */
export const POLL_MS = 3000;

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

/** 剩餘整數秒數顯示成「9 分 05 秒」。 */
export function formatRemaining(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m} 分 ${String(s).padStart(2, "0")} 秒`;
}

class HttpError extends Error {
  constructor(readonly status: number) {
    super(`HTTP ${status}`);
  }
}

/** LINE 沒設定（後端三個端點都回 404）時是 `enabled: false`，整個區塊不顯示。 */
type StatusResult = { enabled: false } | { enabled: true; status: LineBindingStatusOut };

async function fetchStatus(signal: AbortSignal): Promise<StatusResult> {
  const { data, response } = await getLineBinding({ signal });
  if (response?.status === 404) return { enabled: false };
  if (!response?.ok || !data) throw new HttpError(response?.status ?? 0);
  return { enabled: true, status: data };
}

type IssuedCode = { code: string; expiresAt: number; issuedAt: number };

/**
 * 帳號頁的「綁定 LINE」（ADR-0033）：產生綁定碼、在 LINE 傳「綁定 123456」完成綁定；
 * 已綁定時可解除，或產生新碼換成另一個 LINE。綁定碼明文只放元件狀態，不進 localStorage 或網址。
 */
export function LineBindingPanel() {
  const queryClient = useQueryClient();
  const [issued, setIssued] = useState<IssuedCode | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [justBound, setJustBound] = useState(false);
  const prevBound = useRef<boolean | null>(null);

  const remaining = issued ? Math.max(0, Math.ceil((issued.expiresAt - now) / 1000)) : 0;
  const waiting = issued !== null && remaining > 0;

  const query = useQuery({
    queryKey: getLineBindingQueryKey(),
    queryFn: ({ signal }) => fetchStatus(signal),
    refetchInterval: waiting ? POLL_MS : false,
    retry: false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: getLineBindingQueryKey() });

  const create = useMutation({
    mutationFn: async () => {
      const { data, response } = await createLineBindCode();
      if (!response?.ok || !data) throw new HttpError(response?.status ?? 0);
      return data;
    },
    onSuccess: async (created) => {
      const issuedAt = Date.now();
      setNow(issuedAt);
      setIssued({
        code: created.code,
        expiresAt: new Date(created.expires_at).getTime(),
        issuedAt,
      });
      setCopied(null);
      setConfirming(false);
      setJustBound(false);
      await refresh();
    },
  });
  const unbind = useMutation({
    mutationFn: async () => {
      const { response } = await deleteLineBinding();
      if (!response?.ok) throw new HttpError(response?.status ?? 0);
    },
    onSuccess: async () => {
      setConfirming(false);
      setIssued(null);
      setJustBound(false);
      await refresh();
    },
  });

  // 倒數：每秒更新目前時間，到期就停
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [waiting]);

  // 到期那一刻再問一次：最後一秒才綁好的也要看得到
  const expired = issued !== null && remaining === 0;
  useEffect(() => {
    if (expired) void queryClient.invalidateQueries({ queryKey: getLineBindingQueryKey() });
  }, [expired, queryClient]);

  // 綁定完成：伺服器說這組碼已經沒有未用的了（只認產生碼之後才抓到的狀態，免得誤判舊資料）
  const result = query.data;
  const fresh = query.dataUpdatedAt >= (issued?.issuedAt ?? Number.POSITIVE_INFINITY);
  const completed =
    waiting && result?.enabled === true && fresh && result.status.code_expires_at === null;
  useEffect(() => {
    if (completed) {
      setIssued(null);
      setJustBound(true);
    }
  }, [completed]);

  const boundNow = result?.enabled === true && result.status.bound;
  useEffect(() => {
    if (!result?.enabled) return;
    if (prevBound.current === true && !boundNow) setJustBound(false); // 舊的「綁定完成」不能留著
    prevBound.current = boundNow;
  }, [result, boundNow]);

  if (query.isPending) return null;
  if (result && !result.enabled) return null;

  const status = result?.enabled ? result.status : null;
  const bound = status?.bound === true;

  const copy = async () => {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.code);
      setCopied("yes");
    } catch {
      setCopied("failed");
    }
  };

  const createError = create.error instanceof HttpError ? create.error.status : null;

  return (
    <section
      aria-labelledby="line-binding-title"
      className="rounded-2xl border border-line/60 bg-white px-3 pt-4 pb-4 shadow-card md:rounded-3xl md:px-4 md:pt-5 md:pb-5"
      data-testid="line-binding"
    >
      <div className="flex items-center gap-2.5 border-b border-line pt-1 pb-3.5 pl-1 md:gap-3.5 md:pt-0 md:pb-[18px] md:pl-2">
        <MailIcon className="size-7 flex-none text-leaf md:size-9" />
        <h2
          id="line-binding-title"
          className="text-xl font-bold leading-[30px] text-heading md:text-2xl md:leading-9"
        >
          綁定 LINE
        </h2>
      </div>
      <div className="flex flex-col gap-4 px-1 pt-4 md:px-2">
        <p className="m-0 t-body text-ink">
          綁定後，可以在 LINE 傳指令給好險有你的 bot，查詢你的比較紀錄與理賠待辦。bot
          只能讀取，不會改動你的資料。
        </p>
        <p className="m-0 t-caption text-muted" data-testid="line-privacy-notice">
          注意：你在 LINE 傳送的訊息與 bot 的回覆，都會經過 LINE 平台。
        </p>

        {query.isError && (
          <div className="space-y-2" role="alert" data-testid="line-status-error">
            <p className="m-0 t-caption-bold text-danger">無法取得 LINE 綁定狀態。</p>
            <Button className={btnSm} onClick={() => void query.refetch()}>
              重試
            </Button>
          </div>
        )}

        {justBound && (
          <p
            role="status"
            className="m-0 rounded-tile border border-brand bg-paper p-4 t-label text-heading"
            data-testid="line-bound-done"
          >
            綁定完成，現在可以在 LINE 傳指令了。
          </p>
        )}

        {status && bound && (
          <div className="space-y-3" data-testid="line-bound">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <Pill tone="sent">已綁定</Pill>
              <span className="t-caption text-muted" data-testid="line-bound-at">
                綁定時間 {status.bound_at ? dateTime(status.bound_at) : "—"}
              </span>
            </div>
            {!confirming && (
              <div className="flex flex-wrap gap-2">
                <Button
                  className={btnSm}
                  onClick={() => setConfirming(true)}
                  data-testid="line-unbind"
                >
                  解除綁定
                </Button>
                <Button
                  className={btnSm}
                  onClick={() => create.mutate()}
                  disabled={create.isPending}
                  data-testid="line-rebind"
                >
                  換一個 LINE 帳號
                </Button>
              </div>
            )}
            {confirming && (
              <div
                className="space-y-3 rounded-tile border border-line bg-paper p-4"
                data-testid="line-unbind-confirm"
              >
                <p className="m-0 t-body text-ink">
                  確定要解除綁定嗎？解除後 LINE
                  就不能再查詢你的資料，之後要再綁定需要重新產生綁定碼。
                </p>
                <div className="flex gap-2">
                  <Button
                    className={btnSmDanger}
                    onClick={() => unbind.mutate()}
                    disabled={unbind.isPending}
                    data-testid="line-unbind-yes"
                  >
                    確定解除
                  </Button>
                  <Button className={btnSm} onClick={() => setConfirming(false)}>
                    保留
                  </Button>
                </div>
              </div>
            )}
            {unbind.isError && (
              <p
                role="alert"
                className="m-0 t-caption-bold text-danger"
                data-testid="line-unbind-error"
              >
                解除綁定失敗，請再試一次。
              </p>
            )}
          </div>
        )}

        {status && !bound && !issued && (
          <div className="space-y-3" data-testid="line-unbound">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <Pill tone="plain">未綁定</Pill>
            </div>
            <p className="m-0 t-body text-ink" data-testid="line-unbound-note">
              如需重新綁定請於網站上登出登入更新狀態
            </p>
            <Button
              className={btnSmPri}
              onClick={() => create.mutate()}
              disabled={create.isPending}
              data-testid="line-create-code"
            >
              產生綁定碼
            </Button>
          </div>
        )}

        {create.isError && (
          <p role="alert" className="m-0 t-caption-bold text-danger" data-testid="line-code-error">
            {createError === 503
              ? "伺服器還沒設好 LINE 綁定所需的金鑰，請聯絡管理者。"
              : "產生綁定碼失敗，請再試一次。"}
          </p>
        )}

        {issued && (
          <div
            className="space-y-3 rounded-tile border border-brand bg-paper p-4"
            data-testid="line-code-box"
          >
            {bound && (
              <p className="m-0 t-caption-bold text-danger" data-testid="line-replace-note">
                綁定完成後，會取代目前綁定的 LINE 帳號。
              </p>
            )}
            <p className="m-0 t-caption text-muted">綁定碼</p>
            <p
              className="m-0 select-all font-mono text-4xl font-bold tracking-[0.25em] text-heading"
              data-testid="line-code"
            >
              {issued.code}
            </p>
            {waiting ? (
              <p className="m-0 t-label text-ink" role="timer" data-testid="line-countdown">
                剩餘 {formatRemaining(remaining)}
              </p>
            ) : (
              <p role="alert" className="m-0 t-label text-danger" data-testid="line-expired">
                綁定碼已過期，請重新產生。
              </p>
            )}
            {waiting && (
              <>
                <ol className="m-0 list-decimal space-y-1 pl-5 t-body text-ink">
                  <li>在 LINE 加好險有你的 bot 為好友。</li>
                  <li>
                    對 bot 傳送：<strong>綁定 {issued.code}</strong>
                  </li>
                  <li>回到這個頁面，綁定完成會自動更新。</li>
                </ol>
                <p className="m-0 t-caption text-muted">
                  綁定碼 10 分鐘內有效、只能用一次。重新產生會讓前一組碼立刻失效。
                </p>
              </>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {waiting && (
                <Button className={btnSmPri} onClick={copy} data-testid="line-copy">
                  {copied === "yes" ? "已複製" : "複製綁定碼"}
                </Button>
              )}
              <Button
                className={waiting ? btnSm : btnSmPri}
                onClick={() => create.mutate()}
                disabled={create.isPending}
                data-testid="line-regenerate"
              >
                重新產生
              </Button>
            </div>
            {copied === "failed" && (
              <p
                role="alert"
                className="m-0 t-caption-bold text-danger"
                data-testid="line-copy-failed"
              >
                無法自動複製，請點兩下上面的綁定碼選取後手動複製。
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
