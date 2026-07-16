import type { RankedRoute } from "./api";

export function buildGpx(route: RankedRoute, name: string): string {
  const trackPoints = route.geometry
    .map(
      (point) =>
        `<trkpt lat="${point.lat}" lon="${point.lon}"><ele>${point.elevation_m}</ele></trkpt>`,
    )
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Aware Running Route" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
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
