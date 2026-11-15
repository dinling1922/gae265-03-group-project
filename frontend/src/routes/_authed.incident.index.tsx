import { createFileRoute, redirect } from "@tanstack/react-router";

/** 舊的「全部理賠」已由「全部行程」取代（規格 0-6）；舊網址導到 `/trip`。 */
export const Route = createFileRoute("/_authed/incident/")({
  beforeLoad: () => {
    throw redirect({ to: "/trip", replace: true });
  },
});
