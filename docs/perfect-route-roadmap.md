# Roadmap: from current generator to the "perfect route" engine

Written 2026-07-17. Maps the ideal route-creation spec (12 quality dimensions + strict
optimization order) onto what's actually built, and sequences the gap into phases. Extends —
does not replace — the 7-phase algorithm roadmap and its standing gate: **no self-hosted
routing engine until a benchmark proves ORS is the limiting factor.** That gate has held
through every phase so far and still holds here.

## Where we are (benchmarked 2026-07-17)

- 17/17 benchmark scenarios succeed; 9/17 (53%) return ≥1 `matched` route.
- Restroom mile-range error is **solved**: 0.0 m in every scenario (restroom-first generation).
- Distance accuracy is the sole remaining correctness bottleneck (several ~100–300 m near-misses;
  Tribeca 1130 m and Battery Park 1949 m are real trouble spots).
- Median response time 16.7 s (max 29.7 s) — 20 sequential ORS calls.
- Hard-constraint thresholds already match the ideal spec exactly:
  `MAX_DISTANCE_ERROR_M = 100.0` (±0.1 km) and `MAX_RESTROOM_RANGE_ERROR_M = 500.0` (±0.5 km).

## The two unused levers (key insight)

Most of the ideal spec's "flow" and "surface" dimensions look like they require a self-hosted
graph. They don't — two free data sources get us scoring-side awareness without owning routing:

1. **ORS `extra_info`** — the same directions calls we already make can return per-segment
   `surface`, `waytype`, `steepness`, and `traildifficulty` arrays for a one-line request
   change. Zero extra API calls. Gives: % park/pedestrian path, surface breakdown, stairs
   detection, steep-segment detection.
2. **OSM Overpass API** — free point/polygon queries (traffic signals, pedestrian crossings,
   park boundaries) that can be counted *along* an already-generated route geometry. Gives:
   signal/crossing counts, interruption spacing, longest uninterrupted segment — as ranking
   factors and explanations, without the router itself avoiding them.

The distinction that keeps scope sane: **scoring what a route contains** is cheap (levers
above); **generating routes that optimize for it** requires a custom graph (gated, Phase 5 of
the old roadmap). We do all the scoring-side work first and let the benchmark tell us if
generation-side optimization is ever actually needed.

## Gap analysis vs. the 12 ideal dimensions

| # | Ideal dimension | Current state | Gap |
|---|---|---|---|
| 1 | Absolute correctness | Hard constraints on distance/range ✅; loop start=finish ✅ (ORS); pedestrian-legal ✅ (foot-walking profile) | Amenity reachability threshold is 130 m vs. ideal 25–50 m; no open-hours check; only 53% of scenarios produce a matched route |
| 2 | Continuous running flow | Nothing | No signal/crossing/turn data at all (also currently a product-spec §12 *non-goal* — see "Spec conflict" below) |
| 3 | Natural route shape | `repeated_segment_ratio` only | No sharp-turn/U-turn/zigzag detection; repair can create appendages unchecked |
| 4 | Precise distance repair | Single strategy (nudge furthest point along bearing, ≤3 rounds, 8-call budget) | One strategy, fixed halving step, repairs judged on distance only (not shape/segments it adds) |
| 5 | Intelligent amenity placement | Restroom-first generation ✅ — range error now 0.0 everywhere | Hours/seasonality ignored (only a +0.3 confidence bonus if the field exists); entrance-side/fence problem unaddressed (data-limited) |
| 6 | Elevation profile | Total-gain buckets (flat <10 / moderate 10–25 / hilly >25 m/km); per-point elevation already stored but unused | No profile analysis (climb detection, steepness distribution, smoothing of known-noisy ORS DEM) |
| 7 | Surface & environment | Nothing | `extra_info` unlocks most of it cheaply; lighting/wind exposure are out of scope |
| 8 | Training-specific routes | Single `elevation_preference` input | No workout-type input; but it's just a preset mapping onto weights once flow metrics exist |
| 9 | Personalization | Nothing | Requires accounts + history; out of portfolio scope |
| 10 | Route diversity | Similarity penalty (cell-overlap vs. higher-ranked) ✅ — the right mechanism | No archetype guarantee (best / smoothest / most scenic) — needs flow+scenery metrics first |
| 11 | Real-time awareness | Nothing | Closures/weather/events: out of portfolio scope |
| 12 | Transparent explanations | 11 explainability fields in API; UI shows raw normalized floats + matched badge | Users see `0.714`-style numbers, not "2 traffic signals, longest uninterrupted stretch 2.4 km, restroom 18 m off-route" |

## Spec conflict to resolve

`docs/product-spec.md` §12 explicitly lists **traffic-light optimization** and
**water-fountain planning** as non-goals. The ideal spec includes both. This plan treats
interruption *scoring* (Phase C) as in-scope and interruption-optimized *generation* plus
water fountains as still-out — but if we commit to Phase C, product-spec §12 should be
amended so the spec and the code don't disagree.

---

## Phases

Every phase ends with a `benchmark_routes.py` run and a before/after comparison. Ordering
follows the ideal spec's own priority ladder: correctness → amenities → flow → elevation →
shape → surface → diversity. Lower phases never start while a higher one is failing its gate.

### Phase A — Close the two measured gaps (distance accuracy + latency)

This is the already-open decision from last session; do both, in this order.

**A1. Repair-heuristic tuning** (recommended first — it attacks the #1 correctness gap, and
correctness outranks everything in the ideal ordering):
- Adaptive step sizing: current fixed "half the remaining error" halving can oscillate or
  stall; use the measured Δdistance-per-meter-nudged from the previous round to size the next.
- Overshoot handling: nudge *inward* when repair overshoots (currently only outward).
- Try 2–3 anchor points (not just the single furthest point) for stubborn candidates like
  Tribeca/Battery Park — waterfront dead-ends make "furthest point" a bad anchor there.
- Repair fallback-pool candidates too when the matched pool is empty (today near-misses
  outside the 15% `NEAR_MISS_RATIO` are never touched — Battery Park's 1949 m error case).
- Budget: keep the shared 8-call ceiling; smarter spending, not more spending.
- **Gate: ≥75% of the 17 scenarios return ≥1 matched route** (from 53%).

**A2. Parallelize ORS calls**: the 8 blind + 4 restroom-first generation calls are
embarrassingly parallel (`httpx.AsyncClient` + `asyncio.gather`, chunked to respect the
~40/min limit). Repair rounds stay sequential per candidate (each depends on the last) but
can run across candidates concurrently.
- **Gate: median response < 8 s** (from 16.7 s), benchmark match-rate unchanged.

**A3. Benchmark persistence**: write each run's per-scenario results as timestamped JSON
under `backend/benchmarks/` so before/after comparisons stop being copy-paste from stdout.
Small, do it inside A1.

### Phase B — Correctness hardening (ideal §1, §5)

1. **Structured open-hours**: parse `hours_of_operation` strings into structured intervals
   (NYC data is messy free text — parse the common formats, mark the rest unknown). Add an
   optional `run_time` to the request (default: now). A restroom confidently *closed* at run
   time is excluded from matching (hard, per ideal §1); unknown hours stay a soft
   confidence penalty as today.
2. **Tiered reachability**: keep 130 m as the match ceiling but make `off_route_distance_m`
   a scoring factor and surface it in the UI ("restroom 18 m off-route" vs. "requires a
   ~120 m detour"). Making ≤50 m a hard requirement would gut Manhattan match rates —
   measure the benchmark distribution first, then decide the threshold with real numbers.
3. **Entrance-side problem** (park fences, highways between route and restroom):
   data-limited; cheapest honest mitigation is flagging large `off_route_distance_m`, not
   solving geometry we don't have. Note it; don't build it.

### Phase C — Interruption & environment scoring (ideal §2, §7 partial)

The two-lever phase. No new route generation — richer scoring of what we already generate.

1. **ORS `extra_info`**: add `["surface", "waytype", "steepness"]` to both directions calls.
   Derive per route: % pedestrian/park path, % asphalt/concrete/gravel, contains-stairs flag,
   steep-segment count. Cache-free, call-free.
2. **Overpass interruption layer**: one Overpass query per request bbox (or a one-time
   Manhattan-wide ingest into Supabase, like restrooms — cheaper and rate-limit-proof;
   prefer the ingest) for `highway=traffic_signals`, `highway=crossing`, major-road
   crossings. Count occurrences within ~20 m of the route line; compute **longest
   uninterrupted segment** and **interruption spacing** (the ideal spec's clustered-vs-spread
   insight: score the gaps, not just the count).
3. Add flow factors into the matched-pool composite (weights via AskUserQuestion, as with
   every prior budget/weight decision).
- **Gate: benchmark match-rate unchanged; ranked order visibly favors park/greenway routes
  in Central Park scenarios.**

### Phase D — Shape metrics + human-readable explanations (ideal §3, §12)

1. Pure-geometry shape metrics (no API calls): sharp-turn count (heading change >~60° within
   ~30 m), U-turn detection, out-and-back ratio (already have via repeated segments),
   compactness (area/perimeter of the loop hull) to catch zigzag stitching.
2. Repair-quality check: score a repaired candidate's shape delta, not just its distance —
   reject repairs that fix 100 m by adding an ugly appendage (directly ideal §4's
   "no strange zigzags added merely to fix distance").
3. **Explanation overhaul (frontend)**: replace raw normalized floats with facts —
   "10.03 km · restroom at 5.2 km (18 m off-route) · 46 m gain · 2 signals · longest
   uninterrupted 2.4 km · 72% park paths" — plus one tradeoff line between ranked routes
   ("Route 2: one more traffic light, but 1.8 km more waterfront"). This is where every
   metric from B/C/D becomes user-visible value.

### Phase E — Elevation profiles (ideal §6)

Per-point elevation is already in every `RoutePoint`; it's just never analyzed.
1. Smooth the profile (rolling median) — directly addresses the known ORS DEM noise
   (342 m "ascent" on a flat Midtown loop).
2. Detect climbs (sustained grade over min length), max grade, rolling-hills count.
3. Replace bucket-of-total-gain matching with profile matching per preference: flat = no
   climb >X m and low smoothed gain; hilly = at least one meaningful climb, not noise-sum.
4. Later (optional): numeric gain-range input ("10 km with 150–200 m gain").

### Phase F — Workout presets + guaranteed diversity (ideal §8, §10)

1. `workout_type` request field (easy / tempo / long / hills / intervals) mapping to weight
   presets over the factors built in C–E. Cheap once those factors exist; pointless before.
2. Archetype selection: return best-overall, smoothest (min interruptions), most-scenic
   (max park %) — enforced via the existing segment-overlap similarity machinery, with a
   max-overlap cap between the three picks.

### Deferred indefinitely (out of portfolio scope — flag before ever building)

- **Personalization (§9)** — needs auth, run history, feedback loops.
- **Real-time awareness (§11)** — closures/weather/events feeds.
- **Water fountains** — product-spec §12 non-goal; revisit only as a deliberate spec change
  (the restroom pipeline generalizes to it almost for free if that day comes).
- **Self-hosted routing engine** — same gate as always. The one thing that would justify it:
  if after Phase C the benchmark shows routes *score* badly on interruptions and no amount
  of candidate generation + ranking finds good ones — i.e., we can measure flow but ORS
  can't produce flow-optimized geometry. That's the first evidence that would actually
  clear the gate.

## Budget notes (recurring constraint)

- Phases B–F add **zero** ORS calls: `extra_info` piggybacks on existing calls; Overpass/
  ingested OSM data is separate and free; shape/elevation metrics are pure geometry.
- Only Phase A touches the 20-calls/request ceiling, and only to spend it smarter/faster.
- Overpass has its own fair-use limits — another reason to prefer one-time ingest into
  Supabase (the proven restrooms pattern) over per-request queries.
