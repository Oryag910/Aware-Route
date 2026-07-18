import type { RankedRoute } from "../api";
import { routeFacts, tradeoffLine } from "../explanations";

const ARCHETYPE_LABELS: Record<string, string> = {
  best_overall: "Best overall",
  smoothest: "Smoothest",
  most_scenic: "Most scenic",
};

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
  const bestRoute = routes[0] ?? null;

  return (
    <section>
      <h2>Route Options</h2>

      {routes.length < 3 && (
        <p>Found {routes.length} of up to 3 routes.</p>
      )}

      <div>
        {routes.map((route, index) => {
          const isSelected = index === selectedIndex;
          const facts = routeFacts(route);
          const tradeoff =
            index > 0 && bestRoute !== null
              ? tradeoffLine(route, bestRoute)
              : null;

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
                {route.archetype !== null && (
                  <span
                    style={{
                      display: "inline-block",
                      padding: "2px 8px",
                      borderRadius: "4px",
                      fontSize: "0.75rem",
                      fontWeight: "bold",
                      marginLeft: "8px",
                      backgroundColor: "#dbeafe",
                      color: "#1e40af",
                    }}
                  >
                    {ARCHETYPE_LABELS[route.archetype] ?? route.archetype}
                  </span>
                )}
              </h3>

              {!route.matched && (
                <p style={{ color: "#854d0e", fontSize: "0.9rem" }}>
                  Doesn't quite hit your requested distance or restroom
                  range — closest option found.
                </p>
              )}

              <ul
                style={{
                  margin: "8px 0",
                  paddingLeft: "20px",
                  fontSize: "0.9rem",
                }}
              >
                {facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>

              {tradeoff !== null && (
                <p style={{ fontStyle: "italic", fontSize: "0.85rem" }}>
                  {tradeoff}
                </p>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}
