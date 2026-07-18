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
      <h2 className="text-lg font-semibold text-slate-900">
        Route Options
      </h2>

      {routes.length < 3 && (
        <p className="mt-1 text-sm text-slate-500">
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
              className={`block w-full cursor-pointer rounded-lg border p-4 text-left transition-colors hover:border-blue-400 ${
                isSelected
                  ? "border-blue-600 bg-blue-50 ring-2 ring-blue-600"
                  : "border-slate-300 bg-white"
              }`}
            >
              <h3 className="font-semibold text-slate-900">
                Route {index + 1}{" "}
                <span
                  className={`ml-2 inline-block rounded px-2 py-0.5 text-xs font-bold ${
                    route.matched
                      ? "bg-green-100 text-green-800"
                      : "bg-amber-100 text-amber-800"
                  }`}
                >
                  {route.matched ? "Matched" : "Closest available"}
                </span>
                {route.archetype !== null && (
                  <span className="ml-2 inline-block rounded bg-blue-100 px-2 py-0.5 text-xs font-bold text-blue-800">
                    {ARCHETYPE_LABELS[route.archetype] ?? route.archetype}
                  </span>
                )}
              </h3>

              {!route.matched && (
                <p className="mt-1 text-sm text-amber-700">
                  Doesn't quite hit your requested distance or restroom
                  range — closest option found.
                </p>
              )}

              <ul className="my-2 list-disc space-y-0.5 pl-5 text-sm text-slate-600">
                {facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>

              {tradeoff !== null && (
                <p className="text-sm italic text-slate-500">{tradeoff}</p>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}
