import { Button } from "@base-ui/react/button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { deletePolicyMutation, listPoliciesOptions } from "../../client/@tanstack/react-query.gen";
import type { PolicyOut } from "../../client/types.gen";
import { ShieldIcon } from "../../components/icons";
import { btnSm } from "../../components/ui/buttons";

/** 一份保單的說明列：方案、目的地與保險期間（沒有的欄位就略過）。 */
function policyMeta(p: PolicyOut): string {
  const period =
    p.start_date && p.end_date
      ? `${p.start_date.replaceAll("-", "/")}～${p.end_date.replaceAll("-", "/")}`
      : null;
  return [p.plan_name, p.destination, period].filter(Boolean).join(" · ");
}

/**
 * 帳號頁的「我的保單」（ADR-0009）：列出所有保單，每份可以單獨刪除（二次確認）。
 * 刪除連帶這份保單的事故、待辦、文件紀錄、對話與送件紀錄；已存進 Google Drive 的文件不會被刪。
 */
export function MyPoliciesPanel() {
  const policies = useQuery(listPoliciesOptions());
  const queryClient = useQueryClient();
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const remove = useMutation({
    ...deletePolicyMutation(),
    onSuccess: async () => {
      setConfirmingId(null);
      // 行程、事件、對話都可能跟著不見，全部重新讀
      await queryClient.invalidateQueries();
    },
  });

  if (policies.data?.length === 0) return null;
  return (
    <section
      aria-labelledby="my-policies-title"
      className="rounded-2xl border border-line/60 bg-white px-3 pt-4 pb-4 shadow-card md:rounded-3xl md:px-4 md:pt-5 md:pb-5"
      data-testid="my-policies"
    >
      <div className="flex items-center gap-2.5 border-b border-line pt-1 pb-3.5 pl-1 md:gap-3.5 md:pt-0 md:pb-[18px] md:pl-2">
        <ShieldIcon className="size-7 flex-none text-leaf md:size-9" />
        <h2
          id="my-policies-title"
          className="text-xl font-bold leading-[30px] text-heading md:text-2xl md:leading-9"
        >
          我的保單
        </h2>
      </div>
      <div className="space-y-3 px-1 pt-4 md:px-2">
        {policies.isPending && <p className="m-0 t-body text-muted">載入中…</p>}
        {policies.isError && (
          <p role="alert" className="m-0 t-caption-bold text-danger">
            無法取得保單，請重新整理再試一次。
          </p>
        )}
        <ul className="m-0 list-none space-y-3 p-0">
          {policies.data?.map((p) => (
            <li key={p.id} className="space-y-3" data-testid="my-policy">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <span className="min-w-0 flex-1">
                  <span className="block t-label text-ink" data-testid="my-policy-name">
                    {p.display_name}
                  </span>
                  <span className="block t-caption text-muted">{policyMeta(p)}</span>
                </span>
                {confirmingId !== p.id && (
                  <Button
                    className={btnSm}
                    onClick={() => setConfirmingId(p.id)}
                    data-testid="my-policy-delete"
                  >
                    刪除
                  </Button>
                )}
              </div>
              {confirmingId === p.id && (
                <div
                  className="space-y-3 rounded-tile border border-line bg-paper p-4"
                  data-testid="my-policy-confirm"
                >
                  <p className="m-0 t-body text-ink">
                    確定要刪除這份保單嗎？它的行程、事故、待辦與文件紀錄、對話都會一起刪除，無法復原。
                    已存進你 Google Drive 的文件與 Gmail 裡的草稿不會被刪除，請自行到 Google 刪除。
                  </p>
                  <div className="flex gap-2">
                    <Button
                      className={btnSm}
                      onClick={() => remove.mutate({ path: { policy_id: p.id } })}
                      disabled={remove.isPending}
                      data-testid="my-policy-delete-yes"
                    >
                      確定刪除
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
        {remove.isError && (
          <p role="alert" className="m-0 t-caption-bold text-danger">
            刪除失敗，請再試一次。
          </p>
        )}
      </div>
    </section>
  );
}
