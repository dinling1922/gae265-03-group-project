import { createFileRoute, Link } from "@tanstack/react-router";
import { useRef } from "react";
import { LoadingOverlay } from "../components/ui/LoadingOverlay";
import { TripPage } from "../features/trip/TripPage";
import { useTrip } from "../features/trip/useTrip";

export const Route = createFileRoute("/_authed/trip/$policyId/")({
  // `?incident=<事件 id>`：從某個事故導過來時，預設選中並展開那個事故（問完事故、首頁的列都用）
  validateSearch: (search: Record<string, unknown>): { incident?: string } => ({
    incident: typeof search.incident === "string" ? search.incident : undefined,
  }),
  component: TripRoute,
});

/** 行程頁（ADR-0029）：網址 `/trip/$policyId`，行程 ＝ 保單。只有至少有一個事故的保單才有行程頁。 */
function TripRoute() {
  const { policyId } = Route.useParams();
  const { incident } = Route.useSearch();
  const { user } = Route.useRouteContext();
  const { data: trip, error, isPending, isFetching } = useTrip(policyId);

  // 從事故導過來（`?incident=`）：行程資料還沒包含這個事故（快取是舊的、或還在讀）前先蓋載入遮罩，
  // 不先畫只有舊事故的行程頁（#445）。只等進頁後的第一次讀取：讀完還是沒有（不在這個行程裡）就照常顯示，
  // 忽略那個參數；之後行程頁裡的元件掛上去又重新讀取，不能再蓋回遮罩（會一直換來換去）
  const settled = useRef(false);
  if (!isPending && !isFetching) settled.current = true;
  const waiting =
    incident !== undefined &&
    !settled.current &&
    (isPending || !trip?.incidents.some((i) => i.id === incident));
  if (waiting) return <LoadingOverlay>正在整理您的行程…</LoadingOverlay>;
  if (isPending) return <p className="font-ui">載入中…</p>;
  if (error || !trip) {
    return (
      <p role="alert" className="font-ui text-danger">
        找不到這個行程。
        <Link to="/" className="underline">
          回首頁
        </Link>
      </p>
    );
  }
  return (
    <TripPage
      policyId={policyId}
      trip={trip}
      googleUser={user.login_provider === "google"}
      initialIncidentId={incident}
    />
  );
}
