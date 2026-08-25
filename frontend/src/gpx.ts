import type { GenericRoute } from "./api";

export function buildGpx(route: GenericRoute, name: string): string {
  // Elevation is deliberately not exported -- the underlying elevation
  // data isn't accurate enough to surface to users.
  const trackPoints = route.geometry
    .map((point) => `<trkpt lat="${point.lat}" lon="${point.lon}"/>`)
    .join("\n");

  // Satisfied facility stops become GPX waypoints -- a nice-to-have so the
  // downloaded route also marks the restroom/water stops it matched.
  // Unsatisfied results (no facility, or one outside the requested range)
  // are skipped since they weren't actually planned into the route.
  const waypoints = route.facility_results
    .filter((result) => result.satisfied && result.facility !== null)
    .map((result) => {
      const facility = result.facility!;
      const label = facility.name ?? result.kind;
      return `<wpt lat="${facility.latitude}" lon="${facility.longitude}"><name>${label}</name></wpt>`;
    })
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Aware Running Route" xmlns="http://www.topografix.com/GPX/1/1">
${waypoints ? `${waypoints}\n` : ""}  <trk>
    <name>${name}</name>
    <trkseg>
${trackPoints}
    </trkseg>
  </trk>
</gpx>`;
}

export function downloadGpx(gpxContent: string, filename: string): void {
  const blob = new Blob([gpxContent], { type: "application/gpx+xml" });
  const url = URL.createObjectURL(blob);

  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();

  URL.revokeObjectURL(url);
}
