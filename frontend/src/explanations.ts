import type { GenericRoute } from "./api";

const METERS_PER_MILE = 1609.34;

function formatMiles(meters: number): string {
  return (meters / METERS_PER_MILE).toFixed(2);
}

function sharpTurnsFact(route: GenericRoute): string {
  const turnLabel =
    route.sharp_turn_count === 1
      ? "1 sharp turn"
      : `${route.sharp_turn_count} sharp turns`;

  if (route.u_turn_count > 0) {
    const uTurnLabel =
      route.u_turn_count === 1
        ? "1 U-turn"
        : `${route.u_turn_count} U-turns`;

    return `${turnLabel}, ${uTurnLabel}`;
  }

  return turnLabel;
}

/** Ordered, human-readable facts about a route, for display on its
 * result card. Facility-stop specifics are rendered separately in
 * RouteResults.tsx (one line per facility_results entry), since a route
 * can now carry any number of requested stops rather than a single
 * restroom. */
export function routeFacts(route: GenericRoute): string[] {
  const facts: string[] = [];

  facts.push(`${formatMiles(route.distance_m)} mi`);
  facts.push(
    `${Math.round(route.pedestrian_path_ratio * 100)}% park/pedestrian paths`,
  );
  facts.push(sharpTurnsFact(route));

  return facts;
}

/** One comparative sentence for a route vs. the top-ranked route, built
 * from the most significant deltas. Returns null when nothing clears a
 * threshold. */
export function tradeoffLine(
  route: GenericRoute,
  best: GenericRoute,
): string | null {
  let downside: string | null = null;
  let upside: string | null = null;

  const parkDelta =
    route.pedestrian_path_ratio - best.pedestrian_path_ratio;
  if (Math.abs(parkDelta) >= 0.05) {
    const percent = Math.round(Math.abs(parkDelta) * 100);

    if (parkDelta > 0 && upside === null) {
      upside = `${percent}% more park paths`;
    } else if (parkDelta < 0 && downside === null) {
      downside = `${percent}% less park paths`;
    }
  }

  const sharpTurnDelta = route.sharp_turn_count - best.sharp_turn_count;
  if (Math.abs(sharpTurnDelta) >= 2) {
    const count = Math.abs(sharpTurnDelta);
    const label = count === 1 ? "sharp turn" : "sharp turns";

    if (sharpTurnDelta > 0 && downside === null) {
      downside = `${count} more ${label}`;
    } else if (sharpTurnDelta < 0 && upside === null) {
      upside = `${count} fewer ${label}`;
    }
  }

  if (downside === null && upside === null) {
    return null;
  }

  const parts = [downside, upside].filter(
    (part): part is string => part !== null,
  );

  return `${parts.join(", but ")} than Route 1`;
}
