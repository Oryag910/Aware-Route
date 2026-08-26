# backend/data

Committed data assets used by the backend at runtime. See
[`../../DATA_LICENSE.md`](../../DATA_LICENSE.md) and
[`../../ATTRIBUTION.md`](../../ATTRIBUTION.md) — the OpenStreetMap-derived
files below (`manhattan_walk_graph.v1.pkl`, `fountains.json`,
`interruptions.json`) are **not** covered by the repository's MIT
source-code license.

## `manhattan_walk_graph.v1.pkl` / `manhattan_walk_graph.v1.manifest.json`

Versioned Manhattan pedestrian graph used by the local routing engine — a
pickled `networkx.MultiDiGraph` built from OpenStreetMap data via
[OSMnx](https://osmnx.readthedocs.io/), with best-effort SRTM node
elevations attached. Built once offline and committed so the app loads it
at startup instead of fetching/building a graph per request/deploy.

The `.pkl` file contains the OSM-derived graph itself (see
[`DATA_LICENSE.md`](../../DATA_LICENSE.md)); the accompanying
`.manifest.json` is locally generated artifact metadata (version, node/edge
counts, SHA-256, build-environment library versions, source area, whether
elevation is present) used to validate the pickle on load — it is not
itself OSM-derived data and is not separately ODbL-licensed.

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
