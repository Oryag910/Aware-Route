import type { RankedRoute } from "../api";
import { routeFacts, tradeoffLine } from "../explanations";
import { AlertCircle, CheckCircle, Info, Star } from "../icons";

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
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-display font-semibold text-ink">
                  Route {index + 1}
                </h3>

                {route.matched ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-success/15 px-2.5 py-0.5 text-xs font-semibold text-success">
                    <CheckCircle className="h-3.5 w-3.5" />
                    Restroom matched
                  </span>
                ) : route.restroom.kind === "fountain" ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-accent-sky/15 px-2.5 py-0.5 text-xs font-semibold text-accent-sky">
                    <Info className="h-3.5 w-3.5" />
                    Fountain fallback
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2.5 py-0.5 text-xs font-semibold text-warning">
                    <AlertCircle className="h-3.5 w-3.5" />
                    Closest available
                  </span>
                )}

                {route.archetype !== null && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary">
                    <Star className="h-3.5 w-3.5" />
                    {ARCHETYPE_LABELS[route.archetype] ?? route.archetype}
                  </span>
                )}
              </div>

              {!route.matched && route.restroom.kind === "fountain" && (
                <p className="mt-1.5 text-sm text-accent-sky">
                  No eligible restroom matched your requested range. This
                  closest route includes a water fountain instead.
                </p>
              )}

              {!route.matched && route.restroom.kind !== "fountain" && (
                <p className="mt-1.5 text-sm text-warning">
                  Doesn't quite hit your requested distance or restroom range —
                  closest option found.
                </p>
              )}

              <ul className="my-2 list-disc space-y-0.5 pl-5 text-sm text-ink-muted">
                {facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>

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
