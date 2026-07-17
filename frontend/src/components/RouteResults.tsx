import type { RankedRoute } from "../api";

type RouteResultsProps = {
  routes: RankedRoute[];
  selectedIndex: number | null;
  onSelect: (index: number) => void;
};

export default function RouteResults({
  routes,
  selectedIndex,
  onSelect,
}: RouteResultsProps) {
  return (
    <section>
      <h2>Route Options</h2>

      {routes.length < 3 && (
        <p>Found {routes.length} of up to 3 routes.</p>
      )}

      <div>
        {routes.map((route, index) => {
          const isSelected = index === selectedIndex;
          const distanceMiles = (route.distance_m / 1609.34).toFixed(2);
          const restroomMile = (
            route.restroom.mile_marker_m / 1609.34
          ).toFixed(2);
          const offRouteDistanceM = Math.round(
            route.off_route_distance_m,
          );
          const isReachableOnRoute = route.off_route_distance_m <= 50;

          return (
            <button
              key={index}
              type="button"
              onClick={() => onSelect(index)}
              aria-pressed={isSelected}
              style={{
                display: "block",
                width: "100%",
                marginBottom: "12px",
                padding: "16px",
                textAlign: "left",
                cursor: "pointer",
                border: isSelected
                  ? "3px solid #2563eb"
                  : "1px solid #cccccc",
                backgroundColor: isSelected ? "#eff6ff" : "#ffffff",
                borderRadius: "8px",
              }}
            >
              <h3>
                Route {index + 1}{" "}
                <span
                  style={{
                    display: "inline-block",
                    padding: "2px 8px",
                    borderRadius: "4px",
                    fontSize: "0.75rem",
                    fontWeight: "bold",
                    marginLeft: "8px",
                    backgroundColor: route.matched
                      ? "#dcfce7"
                      : "#fef9c3",
                    color: route.matched ? "#166534" : "#854d0e",
                  }}
                >
                  {route.matched ? "Matched" : "Closest available"}
                </span>
              </h3>

              {!route.matched && (
                <p style={{ color: "#854d0e", fontSize: "0.9rem" }}>
                  Doesn't quite hit your requested distance or restroom
                  range — closest option found.
                </p>
              )}

              <p>
                <strong>Distance:</strong> {distanceMiles} miles
              </p>

              <p>
                <strong>Restroom:</strong>{" "}
                {route.restroom.facility_name}
              </p>

              <p>
                <strong>Restroom mile:</strong> {restroomMile}
              </p>

              {isReachableOnRoute ? (
                <p>
                  <strong>Restroom:</strong> {offRouteDistanceM} m
                  off-route
                </p>
              ) : (
                <p style={{ color: "#854d0e" }}>
                  <strong>Restroom:</strong> ~{offRouteDistanceM} m
                  detour required
                </p>
              )}

              <p>
                <strong>Elevation mismatch:</strong>{" "}
                {route.elevation_mismatch.toFixed(3)}
              </p>

              <p>
                <strong>Restroom confidence:</strong>{" "}
                {route.restroom_confidence.toFixed(3)}
              </p>

              <p>
                <strong>Composite score:</strong>{" "}
                {route.composite_score.toFixed(3)}
              </p>
            </button>
          );
        })}
      </div>
    </section>
  );
}
