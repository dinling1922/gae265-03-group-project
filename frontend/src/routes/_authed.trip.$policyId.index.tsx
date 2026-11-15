import { createFileRoute, Link } from "@tanstack/react-router";
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
  const { data: trip, error, isPending } = useTrip(policyId);

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
