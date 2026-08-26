# backend/data

Committed data assets used by the backend at runtime. See
[`../../DATA_LICENSE.md`](../../DATA_LICENSE.md) and
[`../../ATTRIBUTION.md`](../../ATTRIBUTION.md) — these files contain
OpenStreetMap-derived data and are **not** covered by the repository's
MIT source-code license.

## `manhattan_walk_graph.v1.pkl` / `manhattan_walk_graph.v1.manifest.json`

Versioned Manhattan pedestrian graph used by the local routing engine — a
pickled `networkx.MultiDiGraph` built from OpenStreetMap data via
[OSMnx](https://osmnx.readthedocs.io/), with best-effort SRTM node
elevations attached. Built once offline and committed so the app loads it
at startup instead of fetching/building a graph per request/deploy.

Regenerate with `backend/scripts/build_graph.py`. Full format/versioning
details: [`docs/graph-packaging.md`](../../docs/graph-packaging.md).

## `fountains.json`

Manhattan drinking-water locations (`amenity=drinking_water`), extracted
directly from the OSM Overpass API within a Manhattan bounding box.

Regenerate with `backend/scripts/ingest_fountains.py`.

## `interruptions.json`

Manhattan traffic signals and pedestrian crossings (`highway=traffic_signals`,
`highway=crossing`), extracted the same way as `fountains.json`.

Regenerate with `backend/scripts/ingest_interruptions.py`.
