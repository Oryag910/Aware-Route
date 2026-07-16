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
    <main>
      <h1>Aware Running Route</h1>

      <Map
        position={selectedPosition}
        onSelect={setSelectedPosition}
        routes={results}
        selectedRouteIndex={selectedRouteIndex}
      />

      {selectedPosition !== null && (
        <p>
          Selected location: {selectedPosition[0].toFixed(5)},{" "}
          {selectedPosition[1].toFixed(5)}
        </p>
      )}

      <RouteForm
        startPosition={selectedPosition}
        onSubmit={handleRouteSubmit}
        isLoading={isLoading}
      />

      {isLoading && <p>Generating routes...</p>}

      {noRouteFound && (
        <p>
          No route found with a restroom in that mile range near this
          start point — try widening the range or picking a different
          start.
        </p>
      )}

      {error !== null && (
        <p role="alert">
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
        <button type="button" onClick={handleDownloadGpx}>
          Download GPX
        </button>
      )}
    </main>
  );
}

export default App;