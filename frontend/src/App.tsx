import { useRef, useState } from "react";

import {
  ApiError,
  NetworkError,
  fetchRoutes,
  type GenericRoute,
} from "./api";
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

// How long a request runs before the loading state admits the demo backend
// may be waking up from inactivity, rather than just "still searching."
const SLOW_LOADING_MS = 9000;

type ErrorKind =
  | "pin_missing"
  | "no_route"
  | "validation"
  | "rate_limited"
  | "facility_data_unavailable"
  | "routing_unavailable"
  | "network"
  | "server";

type AppError = { kind: ErrorKind; detail?: string };

function classifyError(caughtError: unknown): AppError {
  if (caughtError instanceof NetworkError) {
    return { kind: "network" };
  }

  if (caughtError instanceof ApiError) {
    switch (caughtError.status) {
      case 422:
        return caughtError.isValidationError
          ? { kind: "validation", detail: caughtError.message }
          : { kind: "no_route" };
      case 429:
        return { kind: "rate_limited" };
      case 503:
        return { kind: "facility_data_unavailable" };
      case 502:
        return { kind: "routing_unavailable" };
      default:
        return { kind: "server" };
    }
  }

  return { kind: "server" };
}

function App() {
  const isDesktop = useMediaQuery("(min-width: 768px)");

  const [selectedPosition, setSelectedPosition] = useState<
    [number, number] | null
  >(null);

  const [results, setResults] = useState<GenericRoute[] | null>(null);
  const [selectedRouteIndex, setSelectedRouteIndex] = useState<number | null>(
    null,
  );
  const [appError, setAppError] = useState<AppError | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSlowLoading, setIsSlowLoading] = useState(false);
  const slowLoadingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Remembered inputs so results can collapse the form to a summary and the
  // error state can offer a one-click retry without re-opening the form.
  const [lastValues, setLastValues] = useState<RouteFormValues | null>(null);
  const [showForm, setShowForm] = useState(true);
  const [sheetSnap, setSheetSnap] = useState<SheetSnap>("peek");

  async function runSearch(values: RouteFormValues) {
    if (selectedPosition === null) {
      setAppError({ kind: "pin_missing" });
      return;
    }

    setLastValues(values);
    setAppError(null);
    setResults(null);
    setSelectedRouteIndex(null);
    setIsLoading(true);
    setIsSlowLoading(false);
    setSheetSnap("half");

    if (slowLoadingTimer.current !== null) {
      clearTimeout(slowLoadingTimer.current);
    }
    slowLoadingTimer.current = setTimeout(() => {
      setIsSlowLoading(true);
    }, SLOW_LOADING_MS);

    try {
      const routes = await fetchRoutes(selectedPosition, values);
      setResults(routes);
      setSelectedRouteIndex(routes.length > 0 ? 0 : null);
      setShowForm(false);
    } catch (caughtError) {
      setAppError(classifyError(caughtError));
    } finally {
      if (slowLoadingTimer.current !== null) {
        clearTimeout(slowLoadingTimer.current);
        slowLoadingTimer.current = null;
      }
      setIsLoading(false);
      setIsSlowLoading(false);
    }
  }

  function handleClearStart() {
    if (isLoading) return;
    setSelectedPosition(null);
    setResults(null);
    setSelectedRouteIndex(null);
    setAppError(null);
    setShowForm(true);
  }

  function handleSelectStart(position: [number, number]) {
    if (isLoading) return;
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
          disabled={isLoading}
          className="inline-flex shrink-0 items-center gap-1 rounded font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:cursor-not-allowed disabled:text-ink-muted disabled:no-underline"
        >
          Change
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    ) : null;

  const requirementsSummary =
    lastValues !== null && lastValues.facilityRequirements.length > 0
      ? `${lastValues.facilityRequirements.length} required stop${
          lastValues.facilityRequirements.length === 1 ? "" : "s"
        } · `
      : "";

  const formSummary =
    lastValues !== null ? (
      <div className="flex items-center justify-between gap-2 rounded-lg bg-surface-muted px-3 py-2 text-sm text-ink-muted">
        <span className="truncate">
          {lastValues.targetDistanceMiles} mi · {requirementsSummary}
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
        <StatusMessage
          variant="loading"
          heading={isSlowLoading ? "Still working…" : "Building your routes…"}
        >
          {isSlowLoading
            ? "The demo server may be waking up after inactivity. Your request is still running."
            : "Finding routes that fit your distance, shape, and facility preferences."}
        </StatusMessage>
      )}

      {appError?.kind === "pin_missing" && (
        <StatusMessage variant="info" heading="Set your start point">
          Drop a pin on the map to set your start.
        </StatusMessage>
      )}

      {appError?.kind === "no_route" && (
        <StatusMessage
          variant="info"
          heading="No route found for those settings"
          actions={
            <>
              <InlineAction onClick={() => setShowForm(true)}>
                Adjust settings
              </InlineAction>
              <InlineAction onClick={handleClearStart}>
                Choose a new start
              </InlineAction>
            </>
          }
        >
          We couldn't build a route that fits your target distance and
          requested stops. Try a wider distance or facility mile range, or
          drop the pin somewhere else.
        </StatusMessage>
      )}

      {appError?.kind === "validation" && (
        <StatusMessage variant="info" heading="Check your inputs">
          {appError.detail ??
            "Some of the values you entered aren't valid. Please review and try again."}
        </StatusMessage>
      )}

      {appError?.kind === "rate_limited" && (
        <StatusMessage variant="warning" heading="Demo limit reached">
          This public demo has request limits. Try again later.
        </StatusMessage>
      )}

      {appError?.kind === "facility_data_unavailable" && (
        <StatusMessage
          variant="danger"
          heading="Facility data is temporarily unavailable"
          actions={
            lastValues !== null ? (
              <InlineAction onClick={() => void runSearch(lastValues)}>
                Try again
              </InlineAction>
            ) : undefined
          }
        >
          We couldn't load the facility data needed to build this route. Try
          again in a moment.
        </StatusMessage>
      )}

      {appError?.kind === "routing_unavailable" && (
        <StatusMessage
          variant="danger"
          heading="Routing service is temporarily unavailable"
          actions={
            lastValues !== null ? (
              <InlineAction onClick={() => void runSearch(lastValues)}>
                Try again
              </InlineAction>
            ) : undefined
          }
        >
          We couldn't reach the routing service. Try again in a moment.
        </StatusMessage>
      )}

      {appError?.kind === "network" && (
        <StatusMessage
          variant="danger"
          heading="Couldn't reach the route service"
          actions={
            lastValues !== null ? (
              <InlineAction onClick={() => void runSearch(lastValues)}>
                Try again
              </InlineAction>
            ) : undefined
          }
        >
          Check your connection and try again.
        </StatusMessage>
      )}

      {appError?.kind === "server" && (
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
          An unexpected error occurred. Please try again.
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
            {isDesktop ? "Click" : "Tap"} the map to set your start
          </h2>
          <p className="mt-1 text-sm text-ink-muted">
            We'll build routes around your target distance. Optionally,
            request restroom and water stops within specific mile ranges —
            you can ask for multiple of each.
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
          <div className="absolute inset-0 z-0">{mapEl}</div>
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
