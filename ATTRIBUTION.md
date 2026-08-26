# Attribution

## OpenStreetMap

Map, routing, and facility data used by this project is © OpenStreetMap
contributors: <https://www.openstreetmap.org/copyright>

The committed OSM-derived data assets (Manhattan walk graph, fountains,
interruptions) are governed by the **Open Data Commons Open Database
License (ODbL)**, separately from this repository's source-code license.
See [`DATA_LICENSE.md`](DATA_LICENSE.md) for the full breakdown of which
files this applies to and how each was derived.

## CARTO

The frontend map basemap tiles are served by [CARTO](https://carto.com/attributions)
(`basemaps.cartocdn.com`, Positron/`light_all` style), which are themselves
rendered from OpenStreetMap data. The live map displays both credits:

> © OpenStreetMap contributors © CARTO

See `frontend/src/components/Map.tsx` for the tile layer configuration.

## Elevation (SRTM)

The committed walk graph's node elevations are sourced from NASA's Shuttle
Radar Topography Mission (SRTM), fetched via the `srtm` Python package.
NASA SRTM data is publicly available for reuse; no separate repository
license is applied to the elevation values here. See
[`DATA_LICENSE.md`](DATA_LICENSE.md) for details.
