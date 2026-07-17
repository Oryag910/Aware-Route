import type { RouteFormValues } from "./components/RouteForm";

export type RoutePoint = {
  lat: number;
  lon: number;
  elevation_m: number;
};

export type RestroomInfo = {
  facility_name: string;
  status: string;
  hours_of_operation: string | null;
  latitude: number;
  longitude: number;
  mile_marker_m: number;
};

export type RankedRoute = {
  geometry: RoutePoint[];
  distance_m: number;
  elevation_gain_m: number;
  restroom: RestroomInfo;
  matched: boolean;
  off_route_distance_m: number;
  distance_error_m: number;
  mile_range_error_m: number;
  distance_error_norm: number;
  mile_range_error_norm: number;
  elevation_mismatch: number;
  repeated_segment_ratio: number;
  restroom_confidence: number;
  similarity_penalty: number;
  composite_score: number;
  signal_count: number;
  crossing_count: number;
  longest_uninterrupted_m: number;
  signals_per_km: number;
  pedestrian_path_ratio: number;
  contains_stairs: boolean;
  sharp_turn_count: number;
  u_turn_count: number;
  compactness: number;
  smoothed_gain_m: number;
  climb_count: number;
  longest_climb_m: number;
  longest_climb_grade_pct: number;
  max_grade_pct: number;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function fetchRankedRoutes(
  startPosition: [number, number],
  values: RouteFormValues,
): Promise<RankedRoute[]> {
  const body: Record<string, unknown> = {
    start_lat: startPosition[0],
    start_lon: startPosition[1],
    target_distance_m: values.targetDistanceMiles * 1609.34,
    restroom_min_mile: values.restroomMinMile,
    restroom_max_mile: values.restroomMaxMile,
    elevation_preference: values.elevationPreference,
  };

  // JSON.stringify drops keys whose value is undefined, so run_time
  // is only set (and thus only sent) when the user actually picked a
  // time -- an empty string means "not set," matching the other
  // string-backed inputs in RouteForm.
  if (values.runTime !== "") {
    body.run_time = values.runTime;
  }

  const response = await fetch("/api/routes/with-restroom", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    let detail: string | undefined;

    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail;
    } catch {
      // Response body wasn't valid JSON (e.g. a proxy/gateway error
      // page returned when the backend itself is unreachable).
    }

    throw new ApiError(detail ?? "Request failed", response.status);
  }

  return (await response.json()) as RankedRoute[];
}
