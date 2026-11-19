import { Button } from "@base-ui/react/button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  createApiTokenMutation,
  listApiTokensOptions,
  listApiTokensQueryKey,
  revokeApiTokenMutation,
} from "../../client/@tanstack/react-query.gen";
import type { ApiTokenOut } from "../../client/types.gen";
import { ShieldIcon } from "../../components/icons";
import { btnSm, btnSmPri } from "../../components/ui/buttons";
import { fieldInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Tag";

/** 有效權杖的上限（後端 MAX_ACTIVE_TOKENS，ADR-0032）。 */
export const MAX_ACTIVE_TOKENS = 5;

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

const STATUS_LABEL: Record<ApiTokenOut["status"], string> = {
  active: "有效",
  expired: "已過期",
  revoked: "已撤銷",
};

/** 錯誤本體的 detail 是字串才拿來顯示（409 的說明）；422 是陣列，用通用說法。 */
function errorDetail(error: unknown): string | null {
  const detail = (error as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" ? detail : null;
}

/**
 * 帳號頁的「存取權杖」（ADR-0032）：給 AI 助理唯讀存取比較紀錄與理賠待辦。
 * 明文只在建立當下顯示一次，只放在元件狀態，不進 localStorage，重新整理就消失。
 */
export function ApiTokensPanel() {
  const queryClient = useQueryClient();
  const tokens = useQuery(listApiTokensOptions());
  const [name, setName] = useState("");
  // 預設不勾：新權杖一律先唯讀（ADR-0034）
  const [allowWrite, setAllowWrite] = useState(false);
  const [secret, setSecret] = useState<{ name: string; token: string; canWrite: boolean } | null>(
    null,
  );
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: listApiTokensQueryKey() });
  const create = useMutation({
    ...createApiTokenMutation(),
    onSuccess: async (created) => {
      setSecret({
        name: created.name,
        token: created.token,
        canWrite: created.scopes.includes("write"),
      });
      setCopied(null);
      setName("");
      setAllowWrite(false);
      await refresh();
    },
    // 409 代表畫面上的數量已過期，重新讀才會看到真的已滿
    onError: () => void refresh(),
  });
  const revoke = useMutation({
    ...revokeApiTokenMutation(),
    onSuccess: async () => {
      setConfirmingId(null);
      await refresh();
    },
  });

  const activeCount = tokens.data?.filter((t) => t.status === "active").length ?? 0;
  const full = activeCount >= MAX_ACTIVE_TOKENS;
  const trimmed = name.trim();

  const copy = async () => {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret.token);
      setCopied("yes");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <section
      aria-labelledby="api-tokens-title"
      className="rounded-2xl border border-line/60 bg-white px-3 pt-4 pb-4 shadow-card md:rounded-3xl md:px-4 md:pt-5 md:pb-5"
      data-testid="api-tokens"
    >
      <div className="flex items-center gap-2.5 border-b border-line pt-1 pb-3.5 pl-1 md:gap-3.5 md:pt-0 md:pb-[18px] md:pl-2">
        <ShieldIcon className="size-7 flex-none text-leaf md:size-9" />
        <h2
          id="api-tokens-title"
          className="text-xl font-bold leading-[30px] text-heading md:text-2xl md:leading-9"
        >
          存取權杖
        </h2>
      </div>
      <div className="space-y-4 px-1 pt-4 md:px-2">
        <p className="m-0 t-body text-ink">
          讓 AI 助理讀取你的比較紀錄與理賠待辦；預設唯讀，也可以另外允許它替你建立比較紀錄。
          權杖等同密碼，請只交給你信任的工具；不用時隨時可以撤銷。有效的權杖最多 {MAX_ACTIVE_TOKENS}{" "}
          把。
        </p>

        {secret && (
          <div
            className="space-y-3 rounded-tile border border-brand bg-paper p-4"
            data-testid="api-token-secret-box"
          >
            <p className="m-0 t-label text-heading">
              「{secret.name}」已建立（{secret.canWrite ? "可寫入" : "唯讀"}
              ）。請現在複製並收好，關閉或重新整理後就看不到了。
            </p>
            <p className="m-0 t-caption text-muted" data-testid="api-token-secret-scope">
              {secret.canWrite
                ? "這把權杖可以讀取你的資料，也可以替你建立比較紀錄；不能刪除資料或送出理賠。"
                : "這把權杖只能讀取你的資料，不能建立或修改任何東西。"}
            </p>
            <input
              readOnly
              aria-label="權杖明文"
              value={secret.token}
              onFocus={(e) => e.currentTarget.select()}
              className={`${fieldInput} w-full min-w-0 font-mono text-sm`}
              data-testid="api-token-secret"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button className={btnSmPri} onClick={copy} data-testid="api-token-copy">
                {copied === "yes" ? "已複製" : "複製權杖"}
              </Button>
              <Button
                className={btnSm}
                onClick={() => setSecret(null)}
                data-testid="api-token-secret-close"
              >
                我已收好，關閉
              </Button>
            </div>
            {copied === "failed" && (
              <p role="alert" className="m-0 t-caption-bold text-danger" data-testid="copy-failed">
                無法自動複製，請點上面的欄位選取全部文字後手動複製。
              </p>
            )}
          </div>
        )}

        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (trimmed && !full)
              create.mutate({
                body: { name: trimmed, scopes: allowWrite ? ["read", "write"] : ["read"] },
              });
          }}
        >
          <label htmlFor="api-token-name" className="block t-label text-heading">
            權杖名稱
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id="api-token-name"
              value={name}
              maxLength={50}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：我的 AI 助理"
              disabled={full}
              className={`${fieldInput} min-w-0 flex-1`}
              data-testid="api-token-name"
            />
            <Button
              type="submit"
              className={btnSmPri}
              disabled={!trimmed || full || create.isPending}
              data-testid="api-token-create"
            >
              建立權杖
            </Button>
          </div>
          <label className="flex items-start gap-2 t-body text-ink">
            <input
              type="checkbox"
              checked={allowWrite}
              onChange={(e) => setAllowWrite(e.target.checked)}
              disabled={full}
              className="mt-1 size-4 flex-none accent-brand"
              data-testid="api-token-allow-write"
            />
            <span>允許建立資料（write）</span>
          </label>
          {allowWrite && (
            <p
              role="note"
              className="m-0 rounded-tile border border-warn p-3 t-caption text-warn"
              data-testid="api-token-write-warning"
            >
              這把權杖可以替你建立比較紀錄，但不能刪除資料或送出理賠。請只交給你信任的工具。
            </p>
          )}
          {full && (
            <p className="m-0 t-caption text-warn" data-testid="api-token-limit">
              有效的權杖已達 {MAX_ACTIVE_TOKENS} 把上限，請先撤銷不用的再建立。
            </p>
          )}
          {create.isError && (
            <p
              role="alert"
              className="m-0 t-caption-bold text-danger"
              data-testid="api-token-error"
            >
              {errorDetail(create.error) ?? "建立失敗，請再試一次。"}
            </p>
          )}
        </form>

        {tokens.isPending && <p className="m-0 t-body text-muted">載入中…</p>}
        {tokens.isError && (
          <p role="alert" className="m-0 t-caption-bold text-danger">
            無法取得權杖列表，請重新整理再試一次。
          </p>
        )}
        {tokens.data?.length === 0 && (
          <p className="m-0 t-body text-muted" data-testid="api-tokens-empty">
            還沒有權杖。
          </p>
        )}
        <ul className="m-0 list-none space-y-3 p-0">
          {tokens.data?.map((t) => (
            <li
              key={t.id}
              className={`space-y-3 border-t border-line pt-3 ${t.status === "active" ? "" : "opacity-70"}`}
              data-testid="api-token"
              data-status={t.status}
            >
              <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
                <span className="min-w-0 flex-1">
                  <span
                    className="block break-words t-label text-ink"
                    data-testid="api-token-title"
                  >
                    {t.name}
                  </span>
                  <span className="block t-caption text-muted">
                    <code>{t.prefix}…</code>
                  </span>
                  <span className="block t-caption text-muted">建立 {dateTime(t.created_at)}</span>
                  <span className="block t-caption text-muted">
                    最後使用 {t.last_used_at ? dateTime(t.last_used_at) : "尚未使用"}
                  </span>
                  <span className="block t-caption text-muted">到期 {dateTime(t.expires_at)}</span>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  <Pill tone="plain">
                    <span data-testid="api-token-scope">
                      {t.scopes.includes("write") ? "可寫入" : "唯讀"}
                    </span>
                  </Pill>
                  <Pill tone={t.status === "active" ? "sent" : "plain"}>
                    <span data-testid="api-token-status">{STATUS_LABEL[t.status]}</span>
                  </Pill>
                </span>
                {t.status === "active" && confirmingId !== t.id && (
                  <Button
                    className={btnSm}
                    onClick={() => setConfirmingId(t.id)}
                    data-testid="api-token-revoke"
                  >
                    撤銷
                  </Button>
                )}
              </div>
              {confirmingId === t.id && (
                <div
                  className="space-y-3 rounded-tile border border-line bg-paper p-4"
                  data-testid="api-token-confirm"
                >
                  <p className="m-0 t-body text-ink">
                    確定要撤銷「{t.name}」嗎？使用它的 AI 助理會立刻失去存取權，無法復原。
                  </p>
                  <div className="flex gap-2">
                    <Button
                      className={btnSm}
                      onClick={() => revoke.mutate({ path: { token_id: t.id } })}
                      disabled={revoke.isPending}
                      data-testid="api-token-revoke-yes"
                    >
                      確定撤銷
                    </Button>
                    <Button className={btnSm} onClick={() => setConfirmingId(null)}>
                      保留
                    </Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
        {revoke.isError && (
          <p role="alert" className="m-0 t-caption-bold text-danger">
            撤銷失敗，請再試一次。
          </p>
        )}
      </div>
    </section>
  );
}
