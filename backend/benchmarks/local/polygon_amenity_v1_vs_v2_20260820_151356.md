# Polygon-loop amenity-aware V1 vs V2 comparison (PR #16) — 2026-08-20T15:13:56

**Scope: `shape == "round"`, `amenity_required == True` scenarios only -- 89 of the full 537-scenario suite (includes 2 hard-case scenarios).** `mix`/`out_and_back`/non-amenity scenarios were NOT run through this comparison.

**Offline dataset caveat**: validates amenity-placement MECHANICS against the bundled fountain dataset only (no restroom kind exists offline) -- does NOT validate live Supabase restroom availability or the `/routes/with-restroom` contract; see `tests/test_routes_with_restroom.py` and `tests/generation/test_polygon_amenity.py` for that.

**Measurement note**: every amenity-position number below is the amenity's ACTUAL CUMULATIVE ALONG-ROUTE POSITION on the candidate's real rendered geometry (`app.amenities.matching.match_amenities_to_route`, the same helper `local_scoring.py` uses) -- never shortest graph distance from start.

V1 = `amenity_first.through_amenities_pairs` called directly (amenity-as-turnaround; bypasses the `ROUND_GENERATOR` flag on purpose -- see module docstring). V2 = `engine.generate_polygon_loop_amenity_candidates()` (PR #16 amenity-as-waypoint).

## Reliability

| metric | V1 | V2 |
|---|---|---|
| scenarios run | 89 | 89 |
| scenario success (no exception) | 100.0% | 100.0% |
| scenarios with >=1 candidate | 100.0% | 100.0% |
| amenity-on-route rate (of candidates) | 100.0% | 100.0% |
| amenity-in-range rate (of candidates) | 100.0% | 100.0% |
| scenarios with >=1 candidate within +/-100m | 100.0% | 98.9% |
| scenarios with >=1 candidate BOTH within tolerance AND amenity-in-range | 100.0% | 98.9% |

## Distance

Top-route = the #1-ranked candidate per scenario; scenario-best = the closest-to-target candidate per scenario.

| metric | V1 median | V1 p95 | V2 median | V2 p95 |
|---|---|---|---|---|
| top-route distance error (m) | 43 | 96 | 69 | 278 |
| scenario-best distance error (m) | 8 | 44 | 16 | 65 |

## Amenity mile-range accuracy

Error is 0 when the amenity's actual cumulative position lands inside the requested range, else distance (m) to the nearest bound -- only candidates where the amenity is actually on the route are included.

- V1 mile-range error: median 0m, p95 0m (n=401)
- V2 mile-range error: median 0m, p95 0m (n=429)

## Geometry

### Candidate-pooled

| metric | V1 median | V1 p95 | V2 median | V2 p95 |
|---|---|---|---|---|
| radial exposure | 0.417 | 0.473 | 0.350 | 0.400 |
| elongation | 6.62 | 19.63 | 3.04 | 6.94 |
| compactness | 0.096 | 0.338 | 0.220 | 0.524 |

### Top-ranked route per scenario

| metric | V1 median | V1 p95 | V2 median | V2 p95 |
|---|---|---|---|---|
| radial exposure | 0.414 | 0.466 | 0.331 | 0.394 |
| elongation | 5.23 | 14.28 | 1.72 | 3.88 |
| compactness | 0.177 | 0.464 | 0.397 | 0.621 |

### Defect rates (candidate-pooled)

| defect | V1 | V2 |
|---|---|---|
| excessive repeated segments | 10.2% | 2.8% |
| excessive U-turns | 3.5% | 8.9% |
| short start-return spur | 15.7% | 0.0% |
| disconnected | 0.0% | 0.0% |

## Diversity (rendered-segment overlap)

Same methodology as PR #15's comparison.

| metric | V1 | V2 |
|---|---|---|
| scenarios with >=2 candidates | 88 | 89 |
| exact-duplicate candidate pairs | 1 | 0 |
| median pairwise overlap (pooled) | 0.083 | 0.057 |
| candidate pairs <= 0.80 overlap | 91.5% | 97.0% |
| scenarios where ALL pairs are distinct | 52.3% | 74.2% |

## Latency

- V1: median 0.555s, p95 1.578s, max 2.527s
- V2: median 0.719s, p95 1.941s, max 2.391s

## Hard-case detail

- HARD - Roosevelt Island footbridge, narrow strip: V1 candidates=5 time=0.555s | V2 candidates=5 time=0.967s
- HARD - Brooklyn Bridge approach, tiny + amenity: V1 candidates=5 time=0.164s | V2 candidates=5 time=0.224s
