import L from "leaflet";
import {
  MapContainer,
  Marker,
  Polyline,
  TileLayer,
  useMapEvents,
} from "react-leaflet";

import type { RankedRoute } from "../api";
import icon from "leaflet/dist/images/marker-icon.png";
import iconShadow from "leaflet/dist/images/marker-shadow.png";
import "leaflet/dist/leaflet.css";

L.Icon.Default.mergeOptions({
  iconUrl: icon,
  shadowUrl: iconShadow,
});

type LocationMarkerProps = {
  position: [number, number] | null;
  onSelect: (position: [number, number]) => void;
};

type MapProps = LocationMarkerProps & {
  routes: RankedRoute[] | null;
  selectedRouteIndex: number | null;
};

function LocationMarker({
  position,
  onSelect,
}: LocationMarkerProps) {
  useMapEvents({
    click(event) {
      onSelect([event.latlng.lat, event.latlng.lng]);
    },
  });

  return position ? <Marker position={position} /> : null;
}

export default function Map({
  position,
  onSelect,
  routes,
  selectedRouteIndex,
}: MapProps) {
  return (
    <MapContainer
      center={[40.7831, -73.9712]}
      zoom={13}
      style={{ height: "500px", width: "100%" }}
    >
      <TileLayer
        attribution="© OpenStreetMap contributors"
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      <LocationMarker
        position={position}
        onSelect={onSelect}
      />

      {routes?.map((route, index) => {
        const isSelected = index === selectedRouteIndex;

        return (
          <Polyline
            key={index}
            positions={route.geometry.map(
              (point): [number, number] => [
                point.lat,
                point.lon,
              ],
            )}
            pathOptions={{
              color: isSelected ? "#2563eb" : "#9ca3af",
              weight: isSelected ? 5 : 3,
            }}
          />
        );
      })}

      {routes?.map((route, index) => (
        <Marker
          key={`restroom-${index}`}
          position={[
            route.restroom.latitude,
            route.restroom.longitude,
          ]}
        />
      ))}
    </MapContainer>
  );
}