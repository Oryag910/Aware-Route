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
  // "restroom" | "fountain" — which amenity kind, so the map can pick a
  // distinct marker. Backend defaults to "restroom" (ORS pipeline); the
  // local pipeline sends the real kind.
  kind: "restroom" | "fountain";
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
  archetype: string | null;
};

export class ApiError extends Error {
  status: number;
  // True when `status` is 422 but the body is FastAPI's automatic request-
  // validation shape (`detail` is a list), not the app's deliberate
  // "no route found" 422 (`detail` is a string). Callers use this to tell
  // the two apart without re-inspecting the response body.
  isValidationError: boolean;

  constructor(message: string, status: number, isValidationError = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.isValidationError = isValidationError;
  }
}

// Thrown when `fetch` itself rejects (DNS failure, offline, CORS, backend
// unreachable) rather than returning an HTTP error response -- there's no
// status code or JSON body to inspect in this case.
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

// FastAPI's automatic validation-error shape: `detail` is a list of these.
// The app's own deliberate HTTPExceptions always send a plain string detail.
type ValidationErrorItem = { msg?: unknown };

function parseErrorDetail(body: unknown): {
  message: string;
  isValidationError: boolean;
} {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return { message: "Request failed", isValidationError: false };
  }

  const detail = (body as { detail?: unknown }).detail;

  if (typeof detail === "string") {
    return { message: detail, isValidationError: false };
  }

  if (Array.isArray(detail)) {
    const messages = (detail as ValidationErrorItem[])
      .map((item) => (typeof item.msg === "string" ? item.msg : null))
      .filter((msg): msg is string => msg !== null);

    return {
      message: messages.length > 0 ? messages.join("; ") : "Invalid request.",
      isValidationError: true,
    };
  }

  return { message: "Request failed", isValidationError: false };
}

// API routing scheme:
// - Local dev: VITE_API_URL may point directly at a backend. If it is unset,
//   requests use relative `/api/...` paths and Vite's dev proxy forwards them
//   to localhost after stripping the `/api` prefix.
// - Deployed Vercel builds (production AND previews): always use relative
//   `/api/...` paths. `vercel.json` proxies those server-side to Render, so the
//   browser stays same-origin and never depends on Render CORS allowing each
//   ephemeral Vercel preview hostname.
const API_BASE = import.meta.env.DEV
  ? (import.meta.env.VITE_API_URL as string | undefined ?? "").replace(/\/+$/, "")
  : "";

function apiUrl(path: string): string {
  return API_BASE === "" ? `/api${path}` : `${API_BASE}${path}`;
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
    shape: values.shape,
  };

  // JSON.stringify drops keys whose value is undefined, so run_time
  // is only set (and thus only sent) when the user actually picked a
  // time -- an empty string means "not set," matching the other
  // string-backed inputs in RouteForm.
  if (values.runTime !== "") {
    body.run_time = values.runTime;
  }

  if (values.workoutType !== "") {
    body.workout_type = values.workoutType;
  }

  let response: Response;

  try {
    response = await fetch(apiUrl("/routes/with-restroom"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
  } catch {
    // fetch() rejects (rather than resolving with a non-ok response) when
    // the request never reached a server at all.
    throw new NetworkError(
      "Couldn't reach the route service. Check your connection and try again.",
    );
  }

  if (!response.ok) {
    let message = "Request failed";
    let isValidationError = false;

    try {
      const errorBody: unknown = await response.json();
      ({ message, isValidationError } = parseErrorDetail(errorBody));
    } catch {
      // Response body wasn't valid JSON (e.g. a proxy/gateway error
      // page returned when the backend itself is unreachable).
    }

    throw new ApiError(message, response.status, isValidationError);
  }

  return (await response.json()) as RankedRoute[];
}
