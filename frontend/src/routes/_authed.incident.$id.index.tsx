import { createFileRoute, Navigate } from "@tanstack/react-router";
import { useRef } from "react";
import { ClarifyingView } from "../features/incident/ClarifyingView";
import { useIncident } from "../features/incident/shared";
import { TripPage } from "../features/incident/TripPage";

export const Route = createFileRoute("/_authed/incident/$id/")({
  component: IncidentHome,
});

/**
 * 還在追問時是 2-2 描述事故；問完（有事故類型）導到行程頁（ADR-0029），選中這個事故。
 * 直接打開已經問完的事件（舊網址）暫時還是舊的理賠頁，等整個入口切換（#360）再導向。
 */
function IncidentHome() {
  const { id } = Route.useParams();
  const { user } = Route.useRouteContext();
  const { data: ctx } = useIncident(id);
  // 這次是從追問畫面走過來的（一開始看到的是追問中）：問完那一刻導到行程頁
  const wasClarifying = useRef(false);
  if (!ctx) return null;
  const clarifying = !ctx.incident_type_code && ctx.status !== "completed";
  if (clarifying) {
    wasClarifying.current = true;
    return <ClarifyingView id={id} ctx={ctx} />;
  }
  if (wasClarifying.current && ctx.policy.policy_id) {
    return (
      <Navigate
        to="/trip/$policyId"
        params={{ policyId: ctx.policy.policy_id }}
        search={{ incident: id }}
        replace
      />
    );
  }
  return <TripPage id={id} ctx={ctx} googleUser={user.login_provider === "google"} />;
}
