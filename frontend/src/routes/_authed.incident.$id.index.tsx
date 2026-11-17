import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Navigate, useLocation } from "@tanstack/react-router";
import { useRef } from "react";
import { listIncidentsOptions } from "../client/@tanstack/react-query.gen";
import { LoadingOverlay } from "../components/ui/LoadingOverlay";
import { ClarifyingView } from "../features/incident/ClarifyingView";
import { useIncident, wantsFocusAsk } from "../features/incident/shared";
import { TripPage } from "../features/incident/TripPage";

export const Route = createFileRoute("/_authed/incident/$id/")({
  component: IncidentHome,
});

/**
 * 還在追問、或問完但使用者還沒在 2-2g 彈窗按確認（#345）時是 2-2 描述事故；確認後（有事故類型）導到行程頁（ADR-0029），選中這個事故。
 * 直接打開已經問完的事件（舊網址）暫時還是舊的理賠頁，等整個入口切換（#360）再導向。
 */
function IncidentHome() {
  const { id } = Route.useParams();
  const { user } = Route.useRouteContext();
  const { data: ctx } = useIncident(id);
  // 確認狀態（clarifying／awaiting／confirmed）在事件清單裡，不在給 agent 的 ctx（#345）
  const { data: incidents } = useQuery(listIncidentsOptions());
  // 從建立事故頁送出描述後進來才會帶 focusAsk（路由 state）；直接打開、從首頁或行程頁進來都沒有（#441）
  const focusAsk = useLocation({
    select: (l) => wantsFocusAsk(l.state),
  });
  // 這次是從追問畫面走過來的（一開始看到的是追問中）：問完那一刻導到行程頁
  const wasClarifying = useRef(false);
  if (!ctx || !incidents) return null;
  const confirmation = incidents.find((i) => i.id === id)?.confirmation ?? "confirmed";
  const clarifying =
    confirmation !== "confirmed" || (!ctx.incident_type_code && ctx.status !== "completed");
  // 問完的那一刻：追問畫面留在底下，蓋載入遮罩再導到行程頁；行程頁等資料包含這個事故才拿掉遮罩（#445）
  const leaving = !clarifying && wasClarifying.current && !!ctx.policy.policy_id;
  if (clarifying) wasClarifying.current = true;
  if (clarifying || leaving) {
    return (
      <>
        <ClarifyingView id={id} ctx={ctx} confirmation={confirmation} focusAsk={focusAsk} />
        {leaving && ctx.policy.policy_id && (
          <>
            <LoadingOverlay>正在整理您的行程…</LoadingOverlay>
            <Navigate
              to="/trip/$policyId"
              params={{ policyId: ctx.policy.policy_id }}
              search={{ incident: id }}
              replace
            />
          </>
        )}
      </>
    );
  }
  return <TripPage id={id} ctx={ctx} googleUser={user.login_provider === "google"} />;
}
