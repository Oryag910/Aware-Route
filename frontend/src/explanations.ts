import type { RankedRoute } from "./api";

const METERS_PER_MILE = 1609.34;

// Mirrors RouteResults.tsx's existing off-route-vs-detour distinction:
// within 50m the restroom sits basically on the route, beyond that it's
// a real detour off the running line.
const ON_ROUTE_THRESHOLD_M = 50;

function formatMiles(meters: number): string {
  return (meters / METERS_PER_MILE).toFixed(2);
}

function restroomFact(route: RankedRoute): string {
  const mile = formatMiles(route.restroom.mile_marker_m);
  const name = route.restroom.facility_name;
  const offRouteM = Math.round(route.off_route_distance_m);

  if (route.off_route_distance_m <= ON_ROUTE_THRESHOLD_M) {
    return `Restroom at mile ${mile} (${name}) — ${offRouteM} m off-route`;
  }

  return `Restroom at mile ${mile} (${name}) — ~${offRouteM} m detour required`;
}

function sharpTurnsFact(route: RankedRoute): string {
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
 * result card. */
export function routeFacts(route: RankedRoute): string[] {
  const facts: string[] = [];

  facts.push(`${formatMiles(route.distance_m)} mi`);
  facts.push(restroomFact(route));
  facts.push(`${Math.round(route.elevation_gain_m)} m elevation gain`);
  facts.push(
    `${route.signal_count} traffic signals · ${route.crossing_count} crossings`,
  );
  facts.push(
    `Longest uninterrupted stretch: ${formatMiles(
      route.longest_uninterrupted_m,
    )} mi`,
  );
  facts.push(
    `${Math.round(route.pedestrian_path_ratio * 100)}% park/pedestrian paths`,
  );
  facts.push(sharpTurnsFact(route));

  if (route.contains_stairs) {
    facts.push("Includes stairs");
  }

  return facts;
}

/** One comparative sentence for a route vs. the top-ranked route, built
 * from the most significant deltas. Returns null when nothing clears a
 * threshold. */
export function tradeoffLine(
  route: RankedRoute,
  best: RankedRoute,
): string | null {
  let downside: string | null = null;
  let upside: string | null = null;

  const signalDelta = route.signal_count - best.signal_count;
  if (downside === null && Math.abs(signalDelta) >= 1) {
    const count = Math.abs(signalDelta);
    const label = count === 1 ? "traffic signal" : "traffic signals";

    if (signalDelta > 0) {
      downside = `${count} more ${label}`;
    } else {
      upside = upside ?? `${count} fewer ${label}`;
    }
  }

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

  const elevationDelta = route.elevation_gain_m - best.elevation_gain_m;
  if (Math.abs(elevationDelta) >= 10) {
    const meters = Math.round(Math.abs(elevationDelta));

    if (elevationDelta > 0 && downside === null) {
      downside = `${meters} m more elevation gain`;
    } else if (elevationDelta < 0 && upside === null) {
      upside = `${meters} m less elevation gain`;
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

  const stretchDelta =
    route.longest_uninterrupted_m - best.longest_uninterrupted_m;
  if (Math.abs(stretchDelta) >= 250) {
    const miles = formatMiles(Math.abs(stretchDelta));

    if (stretchDelta > 0 && upside === null) {
      upside = `${miles} mi longer uninterrupted stretch`;
    } else if (stretchDelta < 0 && downside === null) {
      downside = `${miles} mi shorter uninterrupted stretch`;
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
