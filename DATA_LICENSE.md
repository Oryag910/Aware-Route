# Data license

The following three committed data assets are **not** covered by this
repository's source-code license (see [`LICENSE`](LICENSE)):

- `backend/data/manhattan_walk_graph.v1.pkl`
- `backend/data/fountains.json`
- `backend/data/interruptions.json`

These files contain, or are derived from, [OpenStreetMap](https://www.openstreetmap.org/copyright)
data:

> © OpenStreetMap contributors

## How each file is derived

- **`manhattan_walk_graph.v1.pkl`**: the Manhattan pedestrian network fetched
  via [OSMnx](https://osmnx.readthedocs.io/) (`ox.graph_from_place`), which
  queries the OSM Overpass API and returns the walkable way network for the
  requested area. See `backend/scripts/build_graph.py` and
  [`docs/graph-packaging.md`](docs/graph-packaging.md).
- **`fountains.json`**: OSM nodes/ways tagged `amenity=drinking_water`
  within a Manhattan bounding box, fetched directly from the Overpass API.
  See `backend/scripts/ingest_fountains.py`.
- **`interruptions.json`**: OSM nodes tagged `highway=traffic_signals` or
  `highway=crossing` within the same bounding box, fetched the same way.
  See `backend/scripts/ingest_interruptions.py`.

All three are extractions of OSM data with light, mechanical processing
(network filtering, coordinate rounding, bounding-box clipping), not
independently authored datasets. Treat them as OSM-derived database material.

## License terms

OpenStreetMap data is licensed under the **Open Data Commons Open Database
License (ODbL) 1.0**: <https://opendatacommons.org/licenses/odbl/1-0/>.

Per the [OSM copyright page](https://www.openstreetmap.org/copyright) and the
[OSMF attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines),
anyone using these files must:

- credit **OpenStreetMap contributors**, and
- be aware the underlying data is available under the ODbL, and
- understand that extractions/derivatives of the database remain subject to
  ODbL's terms (including its share-alike provisions for redistributed
  derivative databases).

This document is a pointer, not a substitute for the license. Read the
ODbL text at the link above for the actual terms.

## Elevation data (graph artifact only)

`manhattan_walk_graph.v1.pkl` also carries a best-effort `elevation`
attribute on each node, sourced from NASA's Shuttle Radar Topography
Mission (SRTM) via the `srtm` Python package (see `attach_elevations()` in
`backend/scripts/build_graph.py`). NASA SRTM data is publicly available for
reuse; no separate repository license is applied to the elevation values
here, beyond the OSM terms above, which govern the rest of the graph.
