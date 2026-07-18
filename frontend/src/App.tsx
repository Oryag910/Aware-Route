import { useState } from "react";

import {
  ApiError,
  fetchRankedRoutes,
  type RankedRoute,
} from "./api";
import Map from "./components/Map";
import RouteForm, {
  type RouteFormValues,
} from "./components/RouteForm";
import RouteResults from "./components/RouteResults";
import { buildGpx, downloadGpx } from "./gpx";

function App() {
  const [selectedPosition, setSelectedPosition] = useState<
    [number, number] | null
  >(null);

  const [results, setResults] = useState<RankedRoute[] | null>(null);
  const [selectedRouteIndex, setSelectedRouteIndex] = useState<
    number | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [noRouteFound, setNoRouteFound] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  async function handleRouteSubmit(values: RouteFormValues) {
    if (selectedPosition === null) {
      setError("Select a starting point on the map first.");
      return;
    }

    setError(null);
    setNoRouteFound(false);
    setResults(null);
    setSelectedRouteIndex(null);
    setIsLoading(true);

    try {
      const rankedRoutes = await fetchRankedRoutes(
        selectedPosition,
        values,
      );

      setResults(rankedRoutes);
    } catch (caughtError) {
      if (caughtError instanceof ApiError && caughtError.status === 422) {
        setNoRouteFound(true);
      } else if (caughtError instanceof Error) {
        setError(caughtError.message);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      setIsLoading(false);
    }
  }

  function handleDownloadGpx() {
    if (results === null || selectedRouteIndex === null) {
      return;
    }

    const selectedRoute = results[selectedRouteIndex];
    const gpxContent = buildGpx(
      selectedRoute,
      `Route ${selectedRouteIndex + 1}`,
    );

    downloadGpx(gpxContent, `route-${selectedRouteIndex + 1}.gpx`);
  }

  return (
    <main className="flex h-screen flex-col">
      <header className="border-b border-slate-200 bg-white px-6 py-4">
        <h1 className="text-2xl font-semibold text-slate-900">Aware</h1>
        <p className="text-sm text-slate-500">
          Restroom-aware running routes — plan runs around where you can
          stop.
        </p>
      </header>

      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col overflow-hidden md:flex-row">
        <div className="w-full space-y-4 overflow-y-auto p-4 md:h-full md:w-[420px]">
          <RouteForm
            startPosition={selectedPosition}
            onSubmit={handleRouteSubmit}
            isLoading={isLoading}
          />

          {selectedPosition !== null && (
            <p className="text-sm text-slate-600">
              Selected location: {selectedPosition[0].toFixed(5)},{" "}
              {selectedPosition[1].toFixed(5)}
            </p>
          )}

          {isLoading && (
            <p className="animate-pulse text-sm text-slate-500">
              Generating routes...
            </p>
          )}

          {noRouteFound && (
            <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              No route found with a restroom in that mile range near this
              start point — try widening the range or picking a different
              start.
            </p>
          )}

          {error !== null && (
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800"
            >
              Error: {error}
            </p>
          )}

          {results !== null && (
            <RouteResults
              routes={results}
              selectedIndex={selectedRouteIndex}
              onSelect={setSelectedRouteIndex}
            />
          )}

          {results !== null && selectedRouteIndex !== null && (
            <button
              type="button"
              onClick={handleDownloadGpx}
              className="w-full rounded-md border border-blue-600 bg-white px-4 py-2 text-sm font-semibold text-blue-600 hover:bg-blue-50"
            >
              Download GPX
            </button>
          )}
        </div>

        <div className="h-[70vh] w-full md:h-full md:flex-1">
          <Map
            position={selectedPosition}
            onSelect={setSelectedPosition}
            routes={results}
            selectedRouteIndex={selectedRouteIndex}
          />
        </div>
      </div>
    </main>
  );
}

export default App;