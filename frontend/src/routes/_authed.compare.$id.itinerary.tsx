import { createFileRoute } from "@tanstack/react-router";
import { ItineraryPage } from "../features/itinerary/ItineraryPage";

export const Route = createFileRoute("/_authed/compare/$id/itinerary")({
  component: ItineraryRoute,
});

/** 旅遊日程頁（ADR-0031）：1-1，進來時讀回比較紀錄與日程活動。 */
function ItineraryRoute() {
  const { id } = Route.useParams();
  return <ItineraryPage id={id} />;
}
