import type { FacilityResultOut, GenericRoute } from "../api";
import { routeFacts, tradeoffLine } from "../explanations";
import { CheckCircle, XCircle } from "../icons";

const METERS_PER_MILE = 1609.34;

const FACILITY_KIND_LABELS: Record<FacilityResultOut["kind"], string> = {
  restroom: "Restroom",
  water: "Water",
};

function formatMiles(meters: number): string {
  return (meters / METERS_PER_MILE).toFixed(1);
}

function facilityResultLine(result: FacilityResultOut): string {
  const label = FACILITY_KIND_LABELS[result.kind];

  if (result.satisfied && result.facility !== null) {
    const mile = formatMiles(result.facility.mile_marker_m);
    const name = result.facility.name ?? "Unnamed facility";
    return `${label} · ${mile} mi · ${name}`;
  }

  const minMile = formatMiles(result.requested_min_m);
  const maxMile = formatMiles(result.requested_max_m);

  if (result.facility !== null) {
    const foundMile = formatMiles(result.facility.mile_marker_m);
    return `${label} · requested ${minMile}-${maxMile} mi · nearest found ${foundMile} mi`;
  }

  return `${label} · requested ${minMile}-${maxMile} mi · none found nearby`;
}

type RouteResultsProps = {
  routes: GenericRoute[];
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
      <h2 className="font-display text-lg font-semibold text-ink">
        Your routes
      </h2>

      {routes.length < 3 && (
        <p className="mt-1 text-sm text-ink-muted">
          Found {routes.length} of up to 3 routes.
        </p>
      )}

      <div className="mt-3 space-y-3">
        {routes.map((route, index) => {
          const isSelected = index === selectedIndex;
          const facts = routeFacts(route);
          const tradeoff =
            index > 0 && bestRoute !== null
              ? tradeoffLine(route, bestRoute)
              : null;

          const hasRequirements = route.requirements_total > 0;
          const fullMatch =
            hasRequirements &&
            route.requirements_satisfied_count === route.requirements_total;
          const partialMatch =
            hasRequirements &&
            route.requirements_satisfied_count > 0 &&
            route.requirements_satisfied_count < route.requirements_total;
          const noMatch =
            hasRequirements && route.requirements_satisfied_count === 0;

          return (
            <button
              key={index}
              type="button"
              onClick={() => onSelect(index)}
              aria-pressed={isSelected}
              className={`block w-full cursor-pointer rounded-xl border p-4 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 ${
                isSelected
                  ? "border-primary bg-primary/5 ring-2 ring-primary shadow-sm"
                  : "border-border bg-surface hover:border-primary/50 hover:shadow-sm"
              } ${
                !route.constraints_satisfied
                  ? "border-l-4 border-l-warning"
                  : ""
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-display font-semibold text-ink">
                  Route {index + 1}
                </h3>

                {hasRequirements && fullMatch && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-success/15 px-2.5 py-0.5 text-xs font-semibold text-success">
                    <CheckCircle className="h-3.5 w-3.5" />
                    All {route.requirements_total} requested stops matched
                  </span>
                )}

                {hasRequirements && partialMatch && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2.5 py-0.5 text-xs font-semibold text-warning">
                    <CheckCircle className="h-3.5 w-3.5" />
                    {route.requirements_satisfied_count} of{" "}
                    {route.requirements_total} requested stops matched
                  </span>
                )}

                {hasRequirements && noMatch && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-danger/15 px-2.5 py-0.5 text-xs font-semibold text-danger">
                    <XCircle className="h-3.5 w-3.5" />
                    0 of {route.requirements_total} requested stops matched
                  </span>
                )}
              </div>

              {!route.distance_constraint_satisfied && (
                <p className="mt-1.5 text-sm text-warning">
                  Doesn't quite hit your requested distance — closest option
                  found.
                </p>
              )}

              <ul className="my-2 list-disc space-y-0.5 pl-5 text-sm text-ink-muted">
                {facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>

              {route.facility_results.length > 0 && (
                <ul className="my-2 space-y-1 text-sm">
                  {route.facility_results.map((result) => (
                    <li
                      key={result.requirement_id}
                      className={`flex items-center gap-1.5 ${
                        result.satisfied ? "text-ink" : "text-ink-muted"
                      }`}
                    >
                      {result.satisfied ? (
                        <CheckCircle className="h-3.5 w-3.5 shrink-0 text-success" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 shrink-0 text-warning" />
                      )}
                      {facilityResultLine(result)}
                    </li>
                  ))}
                </ul>
              )}

              {tradeoff !== null && (
                <p className="text-sm italic text-ink-muted">{tradeoff}</p>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}
