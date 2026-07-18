# Local-graph route production — migration plan

**Status:** planned, not started (2026-07-18). Supersedes ORS as the route
*generation* engine. Decision: ORS has been benchmarked as the limiting
factor (median 7–30s latency, ±100m repair unreliable, ferry/containment
fights), which satisfies the long-standing "don't own routing until
benchmarked as necessary" gate.

**Decisions locked (2026-07-18):**
- Engine: **in-process OSMnx + NetworkX** graph (not pgRouting, not a
  self-hosted Valhalla/GraphHopper service). Pure Python, full control
  over custom length/shape/amenity search.
- Migration: **keep ORS as a fallback** behind the existing
  `RoutingProvider` Protocol until the local engine benchmarks at parity
  or better, then retire it. Production stays working throughout.

## Why

Every requirement below is something ORS can't do cheaply, because each
new constraint is another black-box round-trip. For a bounded region
(Manhattan), the scalable answer is to build the pedestrian network once,
offline, and run all generation + search in-process. No API in the hot
path → sub-second responses, no rate limits, horizontally scalable
(read-only graph). Side benefit: benchmarking is no longer quota-limited,
so we can run large benchmarks freely.

## Requirements → mechanism

| Requirement | Mechanism |
|---|---|
| Length within ±0.1 km | Local binary-search on turnaround radius (each calc is an in-memory Dijkstra, ~ms) + append a short out-and-back spur or trim a block to absorb the residual |
| Only real walkable ways (no highways/rivers/ferries) | `network_type="walk"` graph excludes them at the data layer — the ferry/containment bug disappears by construction, no `avoid_features`/polygons |
| Restroom **and water-fountain** proximity at requested range | One single-source Dijkstra from start → graph-distance to every node → filter amenities near the requested arc-length, bias generation toward corridors passing one there, score by along-route match |
| Shape: round / out-and-back / mix | Three generation strategies (below) + roundness/linearity metrics weighted by the chosen shape |
| Other enforced metrics | Surface quality (park/trail/greenway vs street), traffic interruptions (existing signals/crossings ingest), elevation-profile match (real DEM grade), turn complexity, park/greenway safety preference |

## Architecture

### Data layer (built once, offline; mirrors existing `ingest_*` scripts)
- **Walk graph** — `scripts/build_graph.py`: OSMnx
  `graph_from_place("Manhattan, New York", network_type="walk")`,
  simplified, serialized to `data/`. Excludes motorways, `foot=no`,
  `route=ferry`, water. Attach real elevation via a DEM
  (`ox.elevation`, SRTM/USGS 1-arc-sec) → per-edge grade.
- **Per-edge attributes** precomputed: length, surface, way type
  (park/trail/greenway/sidewalk/street from `highway`+`leisure=park`),
  grade, interruption count (reuse existing `interruptions.json`).
- **Amenities snapped to nearest node**: existing restrooms **+ water
  fountains** — `scripts/ingest_fountains.py` from OSM
  `amenity=drinking_water` (same OSMnx pipeline as the graph; verify
  coverage in Phase 0) and/or NYC Open Data drinking-fountains dataset.

### Generation (in-process, ms per candidate)
- **Round** — seed waypoints on a ring of radius ≈ target/2π at spread
  bearings; route `start→w1→…→start` with already-used edges penalized so
  the return leg differs → a genuine loop. Maximize the isoperimetric
  quotient (4π·area / perimeter², area via shoelace on the loop).
- **Out-and-back** — shortest path `start→turnaround→start`; choose the
  corridor whose outbound leg is straightest (maximize
  end-displacement ÷ path-length).
- **Mix** — relaxed loop with a low reuse penalty; roundness
  unconstrained, optimize surface/traffic/amenities instead.
- **±100m precision** — binary-search the turnaround radius, then
  spur/trim to absorb the residual.

### Scoring / serving
- Reuse the existing composite scorer (elevation bucket, repeated
  segments, similarity, interruptions, surface, workout presets) but
  computed from edge attributes instead of parsed ORS responses; add
  water-fountain proximity and roundness/linearity as shape-weighted
  factors.
- Graph loaded once at process start (singleton). All requests served
  in-memory.

## Proposed module layout
```
backend/scripts/build_graph.py        # one-time graph + elevation + amenity snap → data/
backend/scripts/ingest_fountains.py   # water fountains (OSM / NYC Open Data)
backend/data/manhattan_walk_graph.*   # serialized graph (build step; likely gitignored by size)
backend/app/graph/loader.py           # load graph singleton
backend/app/graph/distances.py        # snap-to-node + single-source Dijkstra
backend/app/generation/round_route.py
backend/app/generation/out_and_back.py
backend/app/generation/mix.py
backend/app/generation/length_tune.py # binary search + spur/trim
backend/app/generation/amenities.py   # corridor bias to restrooms/fountains
backend/app/routing/local.py          # LocalGraphProvider implementing RoutingProvider (ORS-swappable)
```

## The one real risk to gate on
**Memory footprint on Render free tier (512 MB).** A Manhattan walk graph
in NetworkX with attributes can be 200–500 MB and may not fit alongside
the app. Mitigations: OSMnx `simplify`, strip attributes into compact
arrays, use `igraph` (far lighter), or a paid instance. **Phase 0 must
measure this before anything is built on top.**

## Phased rollout (stays shippable, reuses existing abstractions)
- **Phase 0 — Spike:** build the graph, measure load time + RAM. Gate:
  fits the deploy target.
- **Phase 1:** length-accurate out-and-back + round generation; benchmark
  against the existing 17-scenario `benchmark_routes.py` harness pointed
  at the local engine. Gate: ≥90% within ±100m, <1s median.
- **Phase 2:** amenities (restrooms + fountains) via Dijkstra distances.
- **Phase 3:** surface/elevation/interruption/roundness scoring + shape
  choice wired to the frontend (new `shape` input: round/out-and-back/mix).
- **Phase 4:** swap the endpoint to the local engine behind the
  `RoutingProvider` Protocol; ORS stays as fallback until the benchmark
  confirms parity, then retires.

What survives unchanged: the composite scorer, benchmark harness, restroom
pipeline, frontend, and the `RoutingProvider` Protocol. This replaces
generation, not the whole app.
