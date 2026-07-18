# Graph artifact packaging

The Manhattan walk-network graph (see
[local-graph-routing-plan.md](local-graph-routing-plan.md)) is expensive to
build -- it's an Overpass API fetch of every walkable OSM way in Manhattan,
taking several seconds and requiring network access. Rather than build it
on every deploy or every process start, we build it once offline and commit
the result as a **versioned artifact**.

## Files

| File | Tracked in git? | Purpose |
|---|---|---|
| `backend/data/manhattan_walk_graph.v1.pkl` | **Yes** | The graph itself: a pickled `networkx.MultiDiGraph`, nodes carry `x`/`y` (lon/lat) and `elevation` (meters), edges carry `length` (meters) and optional `geometry`. |
| `backend/data/manhattan_walk_graph.v1.manifest.json` | **Yes** | Metadata + integrity check for the pickle above. |
| `backend/data/manhattan_walk_graph.gpickle` / `.graphml` | No (gitignored) | Legacy unversioned build outputs, kept only for local debugging during the migration. Not loaded by the app. |

The repo-root [`.gitignore`](../.gitignore) ignores
`backend/data/manhattan_walk_graph.*` broadly, then negates the two `v1`
artifact files so they're the only ones actually committed:

```gitignore
backend/data/manhattan_walk_graph.*
!backend/data/manhattan_walk_graph.v1.pkl
!backend/data/manhattan_walk_graph.v1.manifest.json
```

## Manifest schema

`backend/app/graph/packaging.py` defines the `GraphManifest` dataclass:

| Field | Type | Meaning |
|---|---|---|
| `artifact_version` | `str` | Artifact naming version, e.g. `"v1"`. Bump when the pickle format/build process changes in a way that needs a new filename. |
| `graph_schema_version` | `int` | Logical schema of the graph's node/edge attributes (currently `1`). Bump when attributes are added/renamed/removed in a way loader code needs to know about. Checked against `packaging.SUPPORTED_SCHEMA_VERSIONS` on every load. |
| `created_at` | `str` (ISO 8601, UTC) | When the artifact was built. |
| `node_count` / `edge_count` | `int` | Expected graph size, checked against the loaded graph within ±5% tolerance. |
| `sha256` | `str` | SHA-256 of the `.pkl` file's bytes. Verified on every load -- catches corruption, truncation, or a manifest/pickle pairing mismatch. |
| `python_version` / `osmnx_version` / `networkx_version` | `str` | Build environment, for debugging cross-version pickle issues. |
| `source_area` | `str` | The osmnx place query used to build the graph (`"Manhattan, New York, USA"`). |
| `has_elevation` | `bool` | Whether nodes carry an `elevation` attribute (see below). |

Example (current artifact):

```json
{
  "artifact_version": "v1",
  "graph_schema_version": 1,
  "created_at": "2026-07-18T18:09:51.857913+00:00",
  "node_count": 36293,
  "edge_count": 114742,
  "sha256": "587ede358eab17c21acffa6f56176e2710135ae2087aa0a574599ab60c6fd297",
  "python_version": "3.14.3",
  "osmnx_version": "2.1.0",
  "networkx_version": "3.6.1",
  "source_area": "Manhattan, New York, USA",
  "has_elevation": true
}
```

## Validation

`app.graph.packaging.verify_artifact(pkl_path, manifest_path, graph=None)`
runs on every graph load (in `app/graph/loader.py`) and checks, in order:

1. The manifest file exists and is valid JSON with all required fields.
2. `graph_schema_version` is one this code version supports.
3. The pickle file exists and its SHA-256 matches the manifest.
4. Node and edge counts are within ±5% of the manifest's recorded counts.
5. A shortest-path smoke test: pick two nodes from the graph's largest
   weakly-connected component and confirm `networkx.shortest_path` finds a
   route between them. This catches structural corruption that unpickles
   without error but leaves the graph unusable (e.g. a botched
   partial write).

Any failure raises `app.graph.packaging.GraphArtifactError` with a specific
message identifying which check failed. The loader does not catch this --
a broken artifact should fail loudly and immediately rather than serve
degraded routes.

`verify_artifact` accepts an already-loaded `graph` (as the loader does, to
avoid unpickling twice) or will load the pickle itself if called
standalone, e.g. from a CI check or manual debugging session.

## Elevation

Node elevation is attached on a **best-effort basis** during the build
using the free [`srtm.py`](https://pypi.org/project/srtm.py/) package,
which downloads NASA SRTM 1-arc-second tiles on demand (no API key) and
caches them under `~/.cache/srtm`. This avoids needing `rasterio` (heavy
binary dependency, no `manylinux` wheel path that's friction-free here) or
a Google Elevation API key (not free at scale).

Some SRTM pixels are "void" (no data -- common in dense urban areas due to
building shadows in the original radar pass). For the current Manhattan
build, ~11.8% of nodes hit a void pixel on the first lookup. The build
script resolves these in two steps:

1. Search a 3x3 ring of adjacent SRTM grid cells (~28m spacing) for a
   valid neighbor.
2. Any node still unresolved after that falls back to the graph-wide
   median elevation.

This means `has_elevation: true` in the manifest guarantees **every** node
has a numeric `elevation` attribute, even though a small fraction (~9.8% in
the current build) are neighbor/median-filled rather than a direct SRTM
hit. If elevation attachment fails outright (package missing, SRTM source
unreachable, etc.), the build logs this clearly and proceeds without
elevation -- `has_elevation` will be `false` and no `elevation` node
attribute is set. The artifact build never blocks on elevation.

## Rebuilding

```bash
cd backend
python scripts/build_graph.py
```

This re-fetches the walk network from Overpass (takes ~10s), attempts
elevation attachment, writes both the legacy unversioned files and the
versioned `v1.pkl` + `v1.manifest.json`, then self-validates the new
artifact via `verify_artifact` before exiting. Re-running is not
guaranteed to produce byte-identical output (OSM data changes over time),
but the manifest it writes will always validate against the pickle it was
built from.

To bump to a new artifact version (e.g. after a schema change), update
`ARTIFACT_VERSION` / `GRAPH_SCHEMA_VERSION` in `scripts/build_graph.py` and
`GRAPH_PATH` / `MANIFEST_PATH` in `app/graph/loader.py` together, and
update the `.gitignore` negation rules to match the new filenames.

## Loading

`app/graph/loader.py` exposes the same public surface as before
(`load_graph()`, `get_graph()`) -- callers don't need to change. On load it
now also verifies the artifact and logs load time and process RSS:

```
Loaded graph artifact manhattan_walk_graph.v1.pkl in 0.18s (36293 nodes, 114742 edges, RSS=142.3MB)
```

No network access is required to load the graph at runtime -- everything
needed lives in the committed `.pkl`.
