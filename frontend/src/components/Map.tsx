import { useEffect } from "react";

import L from "leaflet";
import {
  MapContainer,
  Marker,
  Polyline,
  useMap,
  useMapEvents,
} from "react-leaflet";

import type { FacilityResultOut, GenericRoute } from "../api";
import "leaflet/dist/leaflet.css";
import "maplibre-gl/dist/maplibre-gl.css";
import "@maplibre/maplibre-gl-leaflet";
import { setWorkerUrl } from "maplibre-gl";

// MapLibre GL resolves its worker script relative to its own module URL at
// runtime, which breaks once a bundler inlines everything into one chunk
// (the worker file never ends up alongside it, and its own relative import
// of maplibre-gl-shared.mjs would break even if it did). vite.config.ts
// copies both files from node_modules/maplibre-gl/dist verbatim so this
// static path always has a matching pair to resolve against.
setWorkerUrl("/vendor/maplibre-gl/maplibre-gl-worker.mjs");

const OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";

// OpenFreeMap's recommended credit line, as clickable links. The MapLibre
// adapter always disables MapLibre GL's own on-canvas attribution control
// (see @maplibre/maplibre-gl-leaflet's _initGL), so this text surfaces only
// through Leaflet's own attribution control -- one attribution surface, not two.
const OPENFREEMAP_ATTRIBUTION =
  '&copy; <a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> ' +
  '&copy; <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> ' +
  'Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';

const ROUTE_COLORS = {
  casing: "#f4f1ea",
  casingSelected: "#dfead9",
  trail: "#6b7a70",
  trailSelected: "#3f6b4f",
} as const;

// Self-contained SVG divIcons — no external image asset / bundler icon fix.
const startIcon = L.divIcon({
  className: "aware-marker aware-marker--start",
  html: `
    <svg width="30" height="38" viewBox="0 0 30 38" xmlns="http://www.w3.org/2000/svg">
      <path d="M15 37C15 37 27 23.6 27 14.5C27 7.04 21.4 1 15 1C8.6 1 3 7.04 3 14.5C3 23.6 15 37 15 37Z"
            fill="#3f6b4f" stroke="#f4f1ea" stroke-width="2"/>
      <circle cx="15" cy="14.5" r="5.5" fill="#f4f1ea"/>
    </svg>`,
  iconSize: [30, 38],
  iconAnchor: [15, 37],
  popupAnchor: [0, -34],
});

// `dimmed` renders a lower-opacity/desaturated marker for facility_results
// entries that were found but fell outside the requested mile window
// (satisfied === false but facility !== null), so unsatisfied stops read
// as visually distinct from satisfied ones without needing a whole second
// marker shape.
function restroomIcon(dimmed: boolean): L.DivIcon {
  return L.divIcon({
    className: "aware-marker aware-marker--restroom",
    html: `
    <svg width="26" height="26" viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg" opacity="${dimmed ? 0.55 : 1}">
      <circle cx="13" cy="13" r="12" fill="#b8894f" stroke="${dimmed ? "#b8894f" : "#f4f1ea"}" stroke-width="2" stroke-dasharray="${dimmed ? "3,2" : "0"}"/>
      <path d="M9 8.5a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2Zm8 0a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2Z" fill="#f4f1ea"/>
      <path d="M7.3 10h3.4c.7 0 1.2.6 1.1 1.3l-.6 6.4a1 1 0 0 1-1 .9H8.8a1 1 0 0 1-1-.9l-.6-6.4A1.1 1.1 0 0 1 7.3 10Z" fill="#f4f1ea"/>
      <path d="M15.3 10h3.4c.6 0 1.1.5 1.1 1.1v4.4c0 .5-.4.9-.9.9h-.3v2.1a.9.9 0 0 1-1.8 0v-2.1h-.3a.9.9 0 0 1-.9-.9v-4.4c0-.6.5-1.1 1.1-1.1Z" fill="#f4f1ea"/>
    </svg>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -13],
  });
}

function waterIcon(dimmed: boolean): L.DivIcon {
  return L.divIcon({
    className: "aware-marker aware-marker--water",
    html: `
    <svg width="26" height="26" viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg" opacity="${dimmed ? 0.55 : 1}">
      <circle cx="13" cy="13" r="12" fill="#4a7fa5" stroke="${dimmed ? "#4a7fa5" : "#f4f1ea"}" stroke-width="2" stroke-dasharray="${dimmed ? "3,2" : "0"}"/>
      <path d="M13 6.5c2.4 3.1 4 5.6 4 7.7a4 4 0 0 1-8 0c0-2.1 1.6-4.6 4-7.7Z" fill="#f4f1ea"/>
    </svg>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -13],
  });
}

function amenityIcon(kind: FacilityResultOut["kind"], dimmed: boolean): L.DivIcon {
  return kind === "water" ? waterIcon(dimmed) : restroomIcon(dimmed);
}

type LocationMarkerProps = {
  position: [number, number] | null;
  onSelect: (position: [number, number]) => void;
};

type MapProps = LocationMarkerProps & {
  routes: GenericRoute[] | null;
  selectedRouteIndex: number | null;
  onSelectRoute: (index: number) => void;
};

function LocationMarker({ position, onSelect }: LocationMarkerProps) {
  useMapEvents({
    click(event) {
      onSelect([event.latlng.lat, event.latlng.lng]);
    },
  });

  return position ? <Marker position={position} icon={startIcon} /> : null;
}

/** OpenFreeMap Positron vector basemap, mounted via the MapLibre-Leaflet adapter. */
function OpenFreeMapBasemap() {
  const map = useMap();

  useEffect(() => {
    const layer = L.maplibreGL({
      style: OPENFREEMAP_STYLE_URL,
      attributionControl: { customAttribution: OPENFREEMAP_ATTRIBUTION },
    });
    layer.addTo(map);

    return () => {
      map.removeLayer(layer);
    };
  }, [map]);

  return null;
}

/** Pan/zoom to fit the current routes whenever they change. */
function FitToRoutes({ routes }: { routes: GenericRoute[] | null }) {
  const map = useMap();

  useEffect(() => {
    if (routes === null || routes.length === 0) return;
    const points = routes.flatMap((route) =>
      route.geometry.map(
        (point): [number, number] => [point.lat, point.lon],
      ),
    );
    if (points.length === 0) return;
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
  }, [routes, map]);

  return null;
}

function MapLegend() {
  return (
    <div className="absolute bottom-3 left-3 z-[1000] space-y-1 rounded-lg bg-surface/90 px-3 py-2 text-xs text-ink shadow-sm backdrop-blur-sm">
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-1 w-4 rounded-full bg-[#3f6b4f]" />
        Selected route
      </div>
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-1 w-4 rounded-full bg-[#6b7a70]" />
        Other routes
      </div>
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-0.5 w-4 border-t-2 border-dotted border-[#6b7a70]" />
        Closest available
      </div>
      <div className="mt-1 flex items-center gap-3">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#b8894f]" />
          Restroom
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#4a7fa5]" />
          Water
        </span>
      </div>
    </div>
  );
}

export default function Map({
  position,
  onSelect,
  routes,
  selectedRouteIndex,
  onSelectRoute,
}: MapProps) {
  // Render unselected routes first, selected last, so the selected pair
  // paints above every other route at overlapping segments.
  const order =
    routes === null
      ? []
      : routes
          .map((_, index) => index)
          .sort(
            (a, b) =>
              (a === selectedRouteIndex ? 1 : 0) -
              (b === selectedRouteIndex ? 1 : 0),
          );

  return (
    <div className="relative h-full w-full">
      <MapContainer
        center={[40.7831, -73.9712]}
        zoom={13}
        className="h-full w-full"
      >
        <OpenFreeMapBasemap />

        <LocationMarker position={position} onSelect={onSelect} />
        <FitToRoutes routes={routes} />

        {order.flatMap((index) => {
          const route = routes![index];
          const isSelected = index === selectedRouteIndex;
          const positions = route.geometry.map(
            (point): [number, number] => [point.lat, point.lon],
          );

          return [
            <Polyline
              key={`casing-${index}`}
              positions={positions}
              interactive={false}
              pathOptions={{
                color: isSelected
                  ? ROUTE_COLORS.casingSelected
                  : ROUTE_COLORS.casing,
                weight: isSelected ? 9 : 6,
                opacity: isSelected ? 0.9 : 0.55,
                lineCap: "round",
                lineJoin: "round",
              }}
            />,
            <Polyline
              key={`line-${index}`}
              positions={positions}
              pathOptions={{
                color: isSelected
                  ? ROUTE_COLORS.trailSelected
                  : ROUTE_COLORS.trail,
                weight: isSelected ? 5 : 3,
                opacity: isSelected ? 1 : 0.75,
                lineCap: "round",
                lineJoin: "round",
                dashArray: route.constraints_satisfied ? undefined : "1, 10",
              }}
              eventHandlers={{
                click: () => onSelectRoute(index),
                mouseover: (event) => {
                  if (isSelected) return;
                  event.target.setStyle({ opacity: 1, weight: 4 });
                },
                mouseout: (event) => {
                  if (isSelected) return;
                  event.target.setStyle({ opacity: 0.75, weight: 3 });
                },
              }}
            />,
          ];
        })}

        {/* Only the selected route's facility stops get markers -- one per
            facility_results entry that actually found a facility, whether
            satisfied or not. Unselected routes stay uncluttered (route
            line only), matching prior behavior. */}
        {selectedRouteIndex !== null &&
          routes?.[selectedRouteIndex]?.facility_results.map((result) => {
            if (result.facility === null) return null;
            const facility = result.facility;

            return (
              <Marker
                key={`facility-${result.requirement_id}`}
                position={[facility.latitude, facility.longitude]}
                icon={amenityIcon(result.kind, !result.satisfied)}
                opacity={result.satisfied ? 1 : 0.7}
                zIndexOffset={result.satisfied ? 1000 : 500}
              />
            );
          })}
      </MapContainer>

      {routes !== null && routes.length > 0 && <MapLegend />}
    </div>
  );
}
