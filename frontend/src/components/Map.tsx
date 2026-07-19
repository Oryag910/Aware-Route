import { useEffect } from "react";

import L from "leaflet";
import {
  MapContainer,
  Marker,
  Polyline,
  TileLayer,
  useMap,
  useMapEvents,
} from "react-leaflet";

import type { RankedRoute, RestroomInfo } from "../api";
import "leaflet/dist/leaflet.css";

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

const restroomIcon = L.divIcon({
  className: "aware-marker aware-marker--restroom",
  html: `
    <svg width="26" height="26" viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg">
      <circle cx="13" cy="13" r="12" fill="#b8894f" stroke="#f4f1ea" stroke-width="2"/>
      <path d="M9 8.5a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2Zm8 0a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2Z" fill="#f4f1ea"/>
      <path d="M7.3 10h3.4c.7 0 1.2.6 1.1 1.3l-.6 6.4a1 1 0 0 1-1 .9H8.8a1 1 0 0 1-1-.9l-.6-6.4A1.1 1.1 0 0 1 7.3 10Z" fill="#f4f1ea"/>
      <path d="M15.3 10h3.4c.6 0 1.1.5 1.1 1.1v4.4c0 .5-.4.9-.9.9h-.3v2.1a.9.9 0 0 1-1.8 0v-2.1h-.3a.9.9 0 0 1-.9-.9v-4.4c0-.6.5-1.1 1.1-1.1Z" fill="#f4f1ea"/>
    </svg>`,
  iconSize: [26, 26],
  iconAnchor: [13, 13],
  popupAnchor: [0, -13],
});

const fountainIcon = L.divIcon({
  className: "aware-marker aware-marker--fountain",
  html: `
    <svg width="26" height="26" viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg">
      <circle cx="13" cy="13" r="12" fill="#4a7fa5" stroke="#f4f1ea" stroke-width="2"/>
      <path d="M13 6.5c2.4 3.1 4 5.6 4 7.7a4 4 0 0 1-8 0c0-2.1 1.6-4.6 4-7.7Z" fill="#f4f1ea"/>
    </svg>`,
  iconSize: [26, 26],
  iconAnchor: [13, 13],
  popupAnchor: [0, -13],
});

function amenityIcon(restroom: RestroomInfo): L.DivIcon {
  return restroom.kind === "fountain" ? fountainIcon : restroomIcon;
}

type LocationMarkerProps = {
  position: [number, number] | null;
  onSelect: (position: [number, number]) => void;
};

type MapProps = LocationMarkerProps & {
  routes: RankedRoute[] | null;
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

/** Pan/zoom to fit the current routes whenever they change. */
function FitToRoutes({ routes }: { routes: RankedRoute[] | null }) {
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
          Fountain
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
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
          subdomains="abcd"
          maxZoom={20}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
        />

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
                dashArray: route.matched ? undefined : "1, 10",
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

        {routes?.map((route, index) => (
          <Marker
            key={`amenity-${index}`}
            position={[route.restroom.latitude, route.restroom.longitude]}
            icon={amenityIcon(route.restroom)}
            opacity={index === selectedRouteIndex ? 1 : 0.55}
            zIndexOffset={index === selectedRouteIndex ? 1000 : 0}
            eventHandlers={{ click: () => onSelectRoute(index) }}
          />
        ))}
      </MapContainer>

      {routes !== null && routes.length > 0 && <MapLegend />}
    </div>
  );
}
