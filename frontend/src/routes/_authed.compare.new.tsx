import { createFileRoute } from "@tanstack/react-router";
import { ItineraryPage } from "../features/itinerary/ItineraryPage";

export const Route = createFileRoute("/_authed/compare/new")({
  component: NewItineraryRoute,
});

/** 空白的旅遊日程頁：目的地與日期都有值時才建立比較紀錄，網址換成 `/compare/$id/itinerary`。 */
function NewItineraryRoute() {
  return <ItineraryPage />;
}
