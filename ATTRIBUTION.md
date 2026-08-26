# Attribution

## OpenStreetMap

Map, routing, and facility data used by this project is © OpenStreetMap
contributors: <https://www.openstreetmap.org/copyright>

The committed OSM-derived data assets (Manhattan walk graph, fountains,
interruptions) are governed by the **Open Data Commons Open Database
License (ODbL)**, separately from this repository's source-code license.
See [`DATA_LICENSE.md`](DATA_LICENSE.md) for the full breakdown of which
files this applies to and how each was derived.

## OpenFreeMap

The frontend map basemap is [OpenFreeMap](https://openfreemap.org)'s
Positron style (`tiles.openfreemap.org/styles/positron`), a vector style
rendered client-side via [MapLibre GL JS](https://maplibre.org/) through the
`@maplibre/maplibre-gl-leaflet` adapter, layered under the existing
React-Leaflet map. OpenFreeMap serves [OpenMapTiles](https://openmaptiles.org/)
vector tiles built from OpenStreetMap data, and requires no API key or
account. The live map displays the credit:

> © OpenFreeMap © OpenMapTiles Data from OpenStreetMap

See `frontend/src/components/Map.tsx` for the basemap layer configuration.

## Elevation (SRTM)

The committed walk graph's node elevations are sourced from NASA's Shuttle
Radar Topography Mission (SRTM), fetched via the `srtm` Python package.
NASA SRTM data is publicly available for reuse; no separate repository
license is applied to the elevation values here. See
[`DATA_LICENSE.md`](DATA_LICENSE.md) for details.
