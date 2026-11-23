import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { createIncident } from "../client";
import {
  getIncidentOptions,
  listIncidentsOptions,
  listPoliciesOptions,
} from "../client/@tanstack/react-query.gen";
import { ActionBar } from "../components/ui/ActionBar";
import { btn, lnk } from "../components/ui/buttons";
import { PageHead } from "../components/ui/PageHead";
import { DescribeChat, FIRST_QUESTION } from "../features/incident/DescribeChat";
import { errorText, FOCUS_ASK_STATE, policyMeta, tripTitle } from "../features/incident/shared";

export const Route = createFileRoute("/_authed/incident/new/$policyId")({
  component: DescribePage,
});

/**
 * 2-2 描述事故（建立前）：保單已經好了，事件還沒有。旅客送出第一句才建立事件並叫起 5 號，
 * 成功後換到 `/incident/$id`（取代歷史紀錄，上一頁不會回到空的對話）。
 * 目的地不用另外問：後端沒帶時用保單的目的地（#203）。
 * 送出後立刻把旅客的話顯示在對話最下面，後面接 5 號輸入中的泡泡（和追問頁一樣），不蓋全頁遮罩；
 * 建立失敗就移除這兩則並顯示錯誤，輸入框保留原本的字。
 */
function DescribePage() {
  const { policyId } = Route.useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const policies = useQuery(listPoliciesOptions());
  const policy = policies.data?.find((p) => p.id === policyId);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  // 剛送出、事件還沒建好的那句話：先顯示在對話裡，換頁後由追問頁的訊息接手
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function start(content: string) {
    if (!content.trim() || busy) return;
    setBusy(true);
    setPending(content);
    setError(null);
    const res = await createIncident({
      path: { policy_id: policyId },
      // 事件要等使用者在 2-2g 確認彈窗按確認才進行程（#345）；行程頁的「新狀況」也一樣（#449）
      body: { description: content, require_confirmation: true },
    });
    if (!res.data) {
      setPending(null);
      setBusy(false);
      return setError(errorText(res.error, res.response?.status));
    }
    // 換頁前先把追問頁要用的資料放進快取，換過去直接有資料：外框（`/incident/$id`）等事件內容、
    // 追問頁等事件清單（確認狀態在清單裡），沒有的話整頁只剩「載入中…」，對話框會消失再出現。
    // 事件內容就在建立事件的回應裡，直接放進快取；事件清單要抓最新的（舊的快取裡還沒有這個事件）。
    // 抓的期間 busy 與輸入中的泡泡維持原樣；清單抓失敗不擋換頁（追問頁自己會再讀）
    const incidentId = res.data.incident.incident_id;
    queryClient.setQueryData(
      getIncidentOptions({ path: { incident_id: incidentId } }).queryKey,
      res.data.incident,
    );
    await queryClient
      .fetchQuery({ ...listIncidentsOptions(), staleTime: 0 })
      .catch(() => undefined);
    await navigate({
      to: "/incident/$id",
      params: { id: incidentId },
      replace: true,
      // 告訴追問頁：旅客剛送出描述，輸入框要自動對焦（只有這一次換頁，#441）
      state: (prev) => ({ ...prev, ...FOCUS_ASK_STATE }),
    });
  }

  if (policies.isPending) return <p className="font-ui">載入中…</p>;
  if (!policy) {
    return (
      <main data-page-paper className="mx-auto flex max-w-[1040px] flex-col gap-4 font-ui md:px-6">
        <p role="alert" className="m-0 t-body text-danger">
          找不到這份保單。
        </p>
        <Link to="/incident/new" className={lnk}>
          回到建立行程
        </Link>
      </main>
    );
  }

  return (
    <main data-page-paper className="mx-auto flex max-w-[1040px] flex-col gap-6 font-ui md:px-6">
      <PageHead
        crumb="出事了：幫我處理理賠 · 描述事故"
        title={tripTitle(policy.destination, policy.start_date, policy.end_date)}
        meta={policyMeta(policy.plan_name, policy.sum_insured)}
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

      <div data-testid="start-form">
        <DescribeChat
          lines={[
            { from: "ai", text: FIRST_QUESTION },
            ...(pending ? [{ from: "me" as const, text: pending }] : []),
          ]}
          quickReplies={[]}
          placeholder="描述發生的事"
          value={text}
          onChange={setText}
          busy={busy}
          typingLabel="正在確認狀況"
          onSend={(content) => void start(content)}
        />
      </div>

      <ActionBar>
        <Link to="/incident/new" search={{ policy: policyId }} className={btn}>
          上一步
        </Link>
        <ActionBar.Push>
          <Link to="/" className={btn}>
            回首頁
          </Link>
        </ActionBar.Push>
      </ActionBar>
    </main>
  );
}
