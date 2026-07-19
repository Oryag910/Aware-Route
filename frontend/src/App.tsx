import { useState } from "react";

import { ApiError, fetchRankedRoutes, type RankedRoute } from "./api";
import AwareMark from "./components/AwareMark";
import BottomSheet, { type SheetSnap } from "./components/BottomSheet";
import Map from "./components/Map";
import RouteForm, { type RouteFormValues } from "./components/RouteForm";
import RouteResults from "./components/RouteResults";
import StatusMessage, { InlineAction } from "./components/StatusMessage";
import { buildGpx, downloadGpx } from "./gpx";
import { Download, MapPin, Pencil, X } from "./icons";
import { useMediaQuery } from "./useMediaQuery";

const SHAPE_LABELS: Record<string, string> = {
  mix: "Mixed",
  round: "Round",
  out_and_back: "Out & back",
};

const ARCHETYPE_LABELS: Record<string, string> = {
  best_overall: "Best overall",
  smoothest: "Smoothest",
  most_scenic: "Most scenic",
};

function App() {
  const isDesktop = useMediaQuery("(min-width: 768px)");

  const [selectedPosition, setSelectedPosition] = useState<
    [number, number] | null
  >(null);

  const [results, setResults] = useState<RankedRoute[] | null>(null);
  const [selectedRouteIndex, setSelectedRouteIndex] = useState<number | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [noRouteFound, setNoRouteFound] = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  // Remembered inputs so results can collapse the form to a summary and the
  // error state can offer a one-click retry without re-opening the form.
  const [lastValues, setLastValues] = useState<RouteFormValues | null>(null);
  const [showForm, setShowForm] = useState(true);
  const [sheetSnap, setSheetSnap] = useState<SheetSnap>("peek");

  async function runSearch(values: RouteFormValues) {
    if (selectedPosition === null) {
      setError("Drop a pin on the map to set your start.");
      return;
    }

    setLastValues(values);
    setError(null);
    setNoRouteFound(false);
    setRateLimited(false);
    setResults(null);
    setSelectedRouteIndex(null);
    setIsLoading(true);
    setSheetSnap("half");

    try {
      const rankedRoutes = await fetchRankedRoutes(selectedPosition, values);
      setResults(rankedRoutes);
      setShowForm(false);
    } catch (caughtError) {
      if (caughtError instanceof ApiError && caughtError.status === 422) {
        setNoRouteFound(true);
      } else if (
        caughtError instanceof ApiError &&
        caughtError.status === 429
      ) {
        setRateLimited(true);
      } else if (caughtError instanceof Error) {
        setError(caughtError.message);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      setIsLoading(false);
    }
  }

  function handleClearStart() {
    setSelectedPosition(null);
    setResults(null);
    setSelectedRouteIndex(null);
    setNoRouteFound(false);
    setError(null);
    setShowForm(true);
  }

  function handleSelectStart(position: [number, number]) {
    setSelectedPosition(position);
    if (!isDesktop) setSheetSnap("half");
  }

  function handleDownloadGpx() {
    if (results === null || selectedRouteIndex === null) return;
    const selectedRoute = results[selectedRouteIndex];
    const gpxContent = buildGpx(selectedRoute, `Route ${selectedRouteIndex + 1}`);
    downloadGpx(gpxContent, `route-${selectedRouteIndex + 1}.gpx`);
  }

  const selectedRoute =
    results !== null && selectedRouteIndex !== null
      ? results[selectedRouteIndex]
      : null;

  // ---- shared control-column pieces (rendered once in the active shell) ----

  const contextStrip =
    selectedPosition !== null ? (
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2 text-sm text-ink-muted">
        <span className="inline-flex items-center gap-1.5 truncate">
          <MapPin className="h-4 w-4 shrink-0 text-primary" />
          <span className="truncate">
            {selectedPosition[0].toFixed(4)}, {selectedPosition[1].toFixed(4)}
          </span>
        </span>
        <button
          type="button"
          onClick={handleClearStart}
          className="inline-flex shrink-0 items-center gap-1 rounded font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
        >
          Change
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    ) : null;

  const formSummary =
    lastValues !== null ? (
      <div className="flex items-center justify-between gap-2 rounded-lg bg-surface-muted px-3 py-2 text-sm text-ink-muted">
        <span className="truncate">
          {lastValues.targetDistanceMiles} mi · {lastValues.restroomMinMile}–
          {lastValues.restroomMaxMile} mi ·{" "}
          {SHAPE_LABELS[lastValues.shape] ?? lastValues.shape}
        </span>
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="inline-flex shrink-0 items-center gap-1 rounded font-semibold text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
        >
          <Pencil className="h-3.5 w-3.5" />
          Edit
        </button>
      </div>
    ) : null;

  const controlContent = (
    <>
      {/* RouteForm stays mounted (state preserved) and hides when collapsed. */}
      <div className={showForm ? "" : "hidden"}>
        <RouteForm
          startPosition={selectedPosition}
          onSubmit={runSearch}
          isLoading={isLoading}
        />
      </div>
      {!showForm && formSummary}

      {isLoading && (
        <StatusMessage variant="loading" heading="Scouting routes…">
          This can take a few seconds while we check nearby restrooms.
        </StatusMessage>
      )}

      {noRouteFound && (
        <StatusMessage
          variant="info"
          heading="No route found in that range"
          actions={
            <>
              <InlineAction onClick={() => setShowForm(true)}>
                Widen restroom range
              </InlineAction>
              <InlineAction onClick={handleClearStart}>
                Choose a new start
              </InlineAction>
            </>
          }
        >
          No loop in that range passes a restroom near this start. Try a wider
          mileage range, or drop the pin somewhere else.
        </StatusMessage>
      )}

      {rateLimited && (
        <StatusMessage variant="warning" heading="Demo limit reached">
          This demo allows a few requests per hour. Try again later.
        </StatusMessage>
      )}

      {error !== null && (
        <StatusMessage
          variant="danger"
          heading="Something went wrong"
          actions={
            lastValues !== null ? (
              <InlineAction onClick={() => void runSearch(lastValues)}>
                Try again
              </InlineAction>
            ) : undefined
          }
        >
          {error}
        </StatusMessage>
      )}

      {results !== null && (
        <RouteResults
          routes={results}
          selectedIndex={selectedRouteIndex}
          onSelect={setSelectedRouteIndex}
        />
      )}
    </>
  );

  const gpxFooter = selectedRoute !== null && (
    <>
      <button
        type="button"
        onClick={handleDownloadGpx}
        className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-primary/40 bg-surface px-4 py-2.5 text-sm font-semibold text-primary transition-colors hover:border-primary hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:bg-primary/10"
      >
        <Download className="h-4 w-4" />
        Download GPX
      </button>
      <p className="mt-1.5 text-center text-xs text-ink-muted">
        Route {selectedRouteIndex! + 1}
        {selectedRoute.archetype !== null &&
          ` · ${ARCHETYPE_LABELS[selectedRoute.archetype] ?? selectedRoute.archetype}`}
      </p>
    </>
  );

  const onboarding =
    selectedPosition === null ? (
      <div className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center p-4">
        <div className="pointer-events-auto max-w-sm rounded-xl border border-border bg-surface/95 px-6 py-5 text-center shadow-lg backdrop-blur">
          <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
            <MapPin className="h-5 w-5 text-primary" />
          </div>
          <h2 className="font-display text-lg font-semibold text-ink">
            Click the map to set your start
          </h2>
          <p className="mt-1 text-sm text-ink-muted">
            We'll build routes that loop back here, passing a restroom or
            fountain in the mile range you choose.
          </p>
        </div>
      </div>
    ) : null;

  const mapEl = (
    <Map
      position={selectedPosition}
      onSelect={handleSelectStart}
      routes={results}
      selectedRouteIndex={selectedRouteIndex}
      onSelectRoute={setSelectedRouteIndex}
    />
  );

  const peekLine =
    selectedPosition === null
      ? "Tap the map to choose a start point"
      : results !== null
        ? `${results.length} route${results.length === 1 ? "" : "s"} found`
        : "Set up your run";

  return (
    <div className="flex h-[100dvh] flex-col bg-background">
      <header className="z-[600] flex-none border-b border-border bg-surface px-4 py-3 md:px-6">
        <div className="flex items-center gap-2">
          <AwareMark className="h-7 w-7" />
          <h1 className="font-display text-2xl font-semibold text-ink">
            Aware
          </h1>
        </div>
        <p className="mt-0.5 hidden text-sm text-ink-muted sm:block">
          Routes that know where you can stop.
        </p>
      </header>

      {isDesktop ? (
        <div className="flex flex-1 overflow-hidden">
          <aside className="flex w-[420px] flex-none flex-col border-r border-border bg-surface">
            {contextStrip}
            <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
              {controlContent}
            </div>
            {gpxFooter && (
              <div className="flex-none border-t border-border px-4 py-3">
                {gpxFooter}
              </div>
            )}
          </aside>
          <div className="relative flex-1">
            {mapEl}
            {onboarding}
          </div>
        </div>
      ) : (
        <div className="relative flex-1 overflow-hidden">
          <div className="absolute inset-0">{mapEl}</div>
          {onboarding}
          <BottomSheet
            snap={sheetSnap}
            onSnapChange={setSheetSnap}
            peek={
              <p className="text-sm font-medium text-ink">{peekLine}</p>
            }
            footer={gpxFooter || undefined}
          >
            {contextStrip}
            <div className="space-y-4 pt-3">{controlContent}</div>
          </BottomSheet>
        </div>
      )}
    </div>
  );
}

export default App;
