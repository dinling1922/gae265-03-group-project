import { createFileRoute, redirect } from "@tanstack/react-router";

/** 舊的 1-1 網址（ADR-0031：已由旅遊日程頁取代）：導到日程頁，舊連結不會壞。 */
export const Route = createFileRoute("/_authed/compare/$id/background")({
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/compare/$id/itinerary", params, replace: true });
  },
});
