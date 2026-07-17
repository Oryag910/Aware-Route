# Phase 1: Hard Constraints on Distance & Restroom Range

This document explains, in full, what changed in this session and why. It's written so that you (or anyone else on the team) could read it cold and understand every decision — not just what the code does, but why it does it that way instead of some other way.

## The problem this solves

Before this change, `/routes/with-restroom` treated "is this route close to what the user asked for" as just another *weighted* scoring factor — `distance_error` was worth 35% of the composite score, `mile_range_error` was worth 30%. That sounds reasonable until you notice the failure mode: if none of the 3-5 candidate routes ORS generated happened to be close to the target distance, the endpoint would still rank them and return the "best of a bad bunch" — e.g. a 7.29mi route for someone who asked for 8mi, presented exactly the same way as a genuine 8.0mi match. The user has no way to tell "this is a great match" from "this is the least-bad option we had."

That's a correctness bug, not a tuning problem. Weights can make something *rank* worse, but they can't make the system say "this doesn't count." Phase 1 fixes that by introducing **hard constraints**: a route either satisfies the user's ask (within tolerance) or it doesn't, and the response now says which.

## The full data flow (request to response)

Here's the path a request takes through `/routes/with-restroom`, end to end, after this change:

1. **Request arrives** at [backend/app/main.py](file:///Users/oryagour/Desktop/run_route/backend/app/main.py) as a `RestroomRouteRequest` — start coordinates, target distance, restroom mile-range, elevation preference, and `count` (1-5, how many results the user wants back).

2. **Candidate generation.** `main.py` calls `get_loop_candidates(provider, start, request.target_distance_m, GENERATE_CANDIDATE_COUNT)` — note it passes the *module constant* `GENERATE_CANDIDATE_COUNT = 12`, not `request.count`. This asks OpenRouteService for 12 distinct loop routes (via [backend/app/routing/candidates.py](file:///Users/oryagour/Desktop/run_route/backend/app/routing/candidates.py), which just calls `provider.get_loop()` once per seed 1..12). `get_loop_candidates` didn't need any code changes — it already accepted any `count`.

3. **Scoring.** All 12 candidates go into `score_and_rank_candidates()` in [backend/app/restrooms/scoring.py](file:///Users/oryagour/Desktop/run_route/backend/app/restrooms/scoring.py) along with the full restroom list. For each candidate:
   - `match_restrooms_to_route()` (unchanged, in `geo.py`) finds every restroom within 130m of the route's geometry (straight-line distance to the nearest point on the route).
   - `best_match_for_range()` picks whichever matched restroom has the smallest mile-range error (i.e. is closest to falling inside `[min_mile_m, max_mile_m]`).
   - If there's no matched restroom at all, the candidate is dropped entirely (same as before this change).
   - If there is one, a `_PartialScore` is built: `distance_error_m` (how far the route's total length is from the target), `mile_range_error_m` (how far the chosen restroom's position is from the requested mile range), `off_route_distance_m` (new — just `best_match.distance_to_route_m`, surfaced for the first time), and a new `matched: bool` flag.

4. **Hard constraint check.** `matched = distance_error_m <= 100.0 and mile_range_error_m <= 500.0`. This is computed once per candidate at partial-score time.

5. **Split into two pools.** All candidates with `matched=True` go into the *matched pool*; everything else goes into the *fallback pool*.

6. **Rank each pool separately** (this is the core structural change — details below): matched candidates are ranked by the renormalized 4-factor composite score (elevation fit, repeated-segment ratio, route similarity, restroom confidence). Fallback candidates are ranked by a much simpler formula: normalized `distance_error + mile_range_error`, because for a fallback the only thing that matters is "how close did we get."

7. **Concatenate**: `scoring.py` returns matched-ranked-first, then fallback-ranked-second — the *entire* pool, not truncated. This is a deliberate return-all-candidates contract (explained below).

8. **422 check** in `main.py`: if `score_and_rank_candidates()` returned an empty list — meaning *zero* candidates had *any* eligible restroom match at all — return a 422. This condition is unchanged from before. A route that fails the distance/range hard constraint but still has a restroom nearby is **not** a 422 case anymore; it's a fallback result.

9. **Slice to `count`** — `main.py` does `scored_candidates[: request.count]`. This is the only place `request.count` is used now for `/routes/with-restroom`; it no longer controls candidate generation.

10. **Build response** — each `ScoredCandidate` becomes a `RankedRouteResponse`, now including the new `matched` and `off_route_distance_m` fields alongside everything that was already there.

## Design decision 1: Why hard constraints instead of weights

Weights answer "which of these is best," hard constraints answer "does this qualify at all." The bug this phase fixes is specifically that the system was answering the first question when the user needed the second one. A 35%-weighted distance factor can never produce a strong enough signal on its own — if every candidate is bad, the "best" one still looks like a normal top-ranked result to the API consumer, because the composite score doesn't carry any absolute meaning (it's a relative, batch-normalized number). Hard constraints attach an absolute meaning: "yes, this is within 100m of your target distance and within 500m of your requested restroom range" or "no, it isn't" — and now that answer is a first-class field (`matched`) in the response instead of being buried in a relative score.

## Design decision 2: Why renormalize instead of leaving the 4 factors at their old absolute weights

The four factors that used to co-exist with distance/range in one 6-factor composite were `elevation_mismatch` (0.15), `repeated_segment_ratio` (0.10), `similarity_penalty` (0.05), and `restroom_confidence` (0.05) — summing to only 0.35 out of 1.0, because the other 0.65 was `distance_error` (0.35) + `mile_range_error` (0.30). Once those two are pulled out into hard constraints, leaving the remaining four at their old absolute values would produce composite scores that only span 0.0-0.35 instead of 0.0-1.0. That's not wrong, exactly, but it makes the score meaningless as an absolute quality indicator, and it makes the relative *contribution* of e.g. `similarity_penalty` (which is meant to be a mild 1/7-ish tiebreaker) disproportionately tiny in a shrunken scale. Renormalizing to sum to 1.0 while preserving the original ratios (15:10:5:5 → 3:2:1:1 → 3/7:2/7:1/7:1/7) keeps the *relative* importance of each factor exactly what it was designed to be, just rescaled onto a full 0-1 range now that it's the only thing doing the ranking.

## Design decision 3: Why fallback gets a separate, simpler ranking instead of reusing the composite

The composite formula (elevation fit + repeated segments + similarity + restroom confidence) answers "which of these *acceptable* routes is the nicest to run." That question doesn't make sense for a fallback candidate — a fallback's defining property is that it *failed* the user's actual ask (wrong distance or restroom too far from where they wanted it). The only thing worth ranking fallbacks by is "how close did we get to what was asked," which is exactly `distance_error` + `mile_range_error`, normalized and averaged 50/50. Reusing the quality composite for fallbacks would imply "these are good, just some are nicer than others" — the wrong message. A separate, deliberately simpler formula keeps that distinction honest.

## Design decision 4: Why count-slicing lives in `main.py`, not `scoring.py`

`score_and_rank_candidates()` doesn't take `request.count` as a parameter at all — it takes `candidates`, `restrooms`, `target_distance_m`, `min_mile_m`, `max_mile_m`, and `preferred_elevation_bucket`. It returns *every* matched-then-fallback candidate in final order. `main.py` is the one that knows about `request.count` and does `scored_candidates[: request.count]` right before building the response.

This is a testability/purity argument: `scoring.py` is pure ranking logic — given a pool of candidates, produce a fully-ordered list. It shouldn't need to know "how many the caller wants," because that's a presentation-layer concern, not a ranking concern. Keeping it out means tests in `test_scoring.py` can assert on the full ranked list without any `count` noise, and if a future caller (e.g. a different endpoint, or a batch job) wants a different slicing policy, it doesn't have to touch scoring at all.

## Design decision 5: Why 12 internal candidates, not 20-50

This was decided and confirmed with you before this plan was written (see the plan's Context section), but the reasoning is worth restating: OpenRouteService's free tier is roughly 2000 requests/day, and each internal candidate costs 1 ORS call. At 20-50 candidates/request, the app would exhaust its daily budget after just 40-100 *user* requests — not viable for anything beyond a demo. At 12/request, the daily budget supports roughly 150-200 user requests/day, which is a much more sustainable ceiling while still giving the scorer 12 candidates to split across matched/fallback pools (versus the old 3-5, where a single bad ORS response could leave you with almost nothing to rank).

## Design decision 6: Why the 130m restroom-proximity threshold in `geo.py` was left untouched

`RESTROOM_PROXIMITY_THRESHOLD_M = 130.0` in [backend/app/restrooms/geo.py](file:///Users/oryagour/Desktop/run_route/backend/app/restrooms/geo.py) answers a completely different question from this phase's hard constraints: "is this restroom physically close enough to the route's path to be reachable at all" (ignores rivers, park boundaries, fences — it's a straight-line/haversine distance). The new `MAX_RESTROOM_RANGE_ERROR_M = 500.0` hard constraint answers "is the restroom's position *along* the route close enough to the mile-marker range the user asked for." A restroom could be 20m off the route (well within the 130m reachability threshold) but still be at mile 5 when the user wanted a restroom between mile 2.5 and 3.5 — that's a range-error failure, not a reachability failure.

Conflating these two would mean re-tuning two different concepts (physical reachability vs. requested-position accuracy) in a single pass, and risked introducing subtle regressions in `match_restrooms_to_route()`'s existing, already-tested behavior. The plan explicitly scoped this out: "avoids re-tuning the composite formula twice in one session. Revisit when Phase 4 (running-quality scoring) touches weights again." `off_route_distance_m` (the new field, sourced directly from `RestroomMatch.distance_to_route_m`) exists purely for *transparency* — so a frontend can eventually show "restroom is ~40m off your route" — not as a new weighted ranking factor.

## Before/after code

### `scoring.py` constants

**Before:**
```python
WEIGHT_DISTANCE_ERROR = 0.35
WEIGHT_MILE_RANGE_ERROR = 0.30
WEIGHT_ELEVATION_MISMATCH = 0.15
WEIGHT_REPEATED_SEGMENT = 0.10
WEIGHT_SIMILARITY_PENALTY = 0.05
WEIGHT_RESTROOM_CONFIDENCE = 0.05
```

**After:**
```python
MAX_DISTANCE_ERROR_M = 100.0
MAX_RESTROOM_RANGE_ERROR_M = 500.0

# Renormalized from the original 15:10:5:5 ratio now that distance_error
# and mile_range_error are hard constraints instead of weighted factors.
# 15:10:5:5 reduces to 3:2:1:1 (dividing by 5), and 3+2+1+1 = 7, so each
# weight becomes its exact share of 7 — this preserves the original
# relative ratios among the four remaining factors.
WEIGHT_ELEVATION_MISMATCH = 3 / 7
WEIGHT_REPEATED_SEGMENT = 2 / 7
WEIGHT_SIMILARITY_PENALTY = 1 / 7
WEIGHT_RESTROOM_CONFIDENCE = 1 / 7

# Fallback candidates (those failing a hard constraint) are ranked by
# combined normalized distance+range error, weighted equally — a
# simpler ranking since the point of a fallback is "closest to what
# was asked," not route-quality nuance.
WEIGHT_FALLBACK_DISTANCE_ERROR = 0.5
WEIGHT_FALLBACK_MILE_RANGE_ERROR = 0.5
```

Why exact fractions (`3 / 7`) instead of decimals? Because `15:10:5:5` doesn't divide evenly into clean decimals when renormalized to sum to 1.0 (`0.15 / 0.35 = 0.42857...`), so using exact fractions keeps the ratio mathematically exact rather than introducing rounding artifacts, and it documents *why* the numbers are what they are directly in the constant.

### `_PartialScore` / `ScoredCandidate` dataclasses

**Before** (`_PartialScore`):
```python
@dataclass(frozen=True)
class _PartialScore:
    candidate: RouteCandidate
    restroom_match: RestroomMatch
    distance_error_m: float
    mile_range_error_m: float
    repeated_segment_ratio: float
    elevation_mismatch: float
    restroom_confidence: float
```

**After:**
```python
@dataclass(frozen=True)
class _PartialScore:
    candidate: RouteCandidate
    restroom_match: RestroomMatch
    distance_error_m: float
    mile_range_error_m: float
    off_route_distance_m: float
    matched: bool
    repeated_segment_ratio: float
    elevation_mismatch: float
    restroom_confidence: float
```

`ScoredCandidate` gained the same two fields (`off_route_distance_m: float`, `matched: bool`) in the same position, right after `mile_range_error_m`.

### `score_and_rank_candidates()` — the core restructure

**Before** (single-pass, one ranking, distance/range as weighted factors):
```python
def score_and_rank_candidates(...) -> list[ScoredCandidate]:
    partial_scores: list[_PartialScore] = []
    for candidate in candidates:
        ...
        if best_match is None:
            continue
        partial_scores.append(_PartialScore(...))

    if not partial_scores:
        return []

    distance_error_norms = normalize_min_max([...])
    mile_range_error_norms = normalize_min_max([...])

    subtotals = [
        WEIGHT_DISTANCE_ERROR * distance_error_norms[index]
        + WEIGHT_MILE_RANGE_ERROR * mile_range_error_norms[index]
        + WEIGHT_ELEVATION_MISMATCH * partial.elevation_mismatch
        + WEIGHT_REPEATED_SEGMENT * partial.repeated_segment_ratio
        + WEIGHT_RESTROOM_CONFIDENCE * (1.0 - partial.restroom_confidence)
        for index, partial in enumerate(partial_scores)
    ]
    # ... similarity pass, then build+sort ScoredCandidate list
    return scored_candidates
```

**After** (partial scores computed the same way, but now split into two pools, each with its own ranking function, then concatenated):
```python
def score_and_rank_candidates(
    candidates: list[RouteCandidate],
    restrooms: list[Restroom],
    target_distance_m: float,
    min_mile_m: float,
    max_mile_m: float,
    preferred_elevation_bucket: str,
) -> list[ScoredCandidate]:
    partial_scores: list[_PartialScore] = []

    for candidate in candidates:
        matches = match_restrooms_to_route(candidate.geometry, restrooms)
        best_match = best_match_for_range(matches, min_mile_m, max_mile_m)

        if best_match is None:
            continue

        distance_error = abs(candidate.distance_m - target_distance_m)
        range_error = mile_range_error_m(best_match.mile_marker_m, min_mile_m, max_mile_m)

        partial_scores.append(
            _PartialScore(
                candidate=candidate,
                restroom_match=best_match,
                distance_error_m=distance_error,
                mile_range_error_m=range_error,
                off_route_distance_m=best_match.distance_to_route_m,
                matched=(
                    distance_error <= MAX_DISTANCE_ERROR_M
                    and range_error <= MAX_RESTROOM_RANGE_ERROR_M
                ),
                repeated_segment_ratio=repeated_segment_ratio(candidate.geometry),
                elevation_mismatch=elevation_mismatch_norm(candidate, preferred_elevation_bucket),
                restroom_confidence=restroom_confidence(best_match.restroom),
            )
        )

    if not partial_scores:
        return []

    distance_error_norms = normalize_min_max([p.distance_error_m for p in partial_scores])
    mile_range_error_norms = normalize_min_max([p.mile_range_error_m for p in partial_scores])

    matched_indices = [i for i, p in enumerate(partial_scores) if p.matched]
    fallback_indices = [i for i, p in enumerate(partial_scores) if not p.matched]

    matched_scored = _rank_matched(
        [partial_scores[i] for i in matched_indices],
        [distance_error_norms[i] for i in matched_indices],
        [mile_range_error_norms[i] for i in matched_indices],
    )
    fallback_scored = _rank_fallback(
        [partial_scores[i] for i in fallback_indices],
        [distance_error_norms[i] for i in fallback_indices],
        [mile_range_error_norms[i] for i in fallback_indices],
    )

    return matched_scored + fallback_scored
```

Note that `distance_error_norms`/`mile_range_error_norms` are computed **once, across the whole pool** (matched + fallback together), then split by index. This matters: it keeps the meaning of "how does this candidate's distance error compare to the others in this request's pool" consistent regardless of which side of the matched/fallback line it landed on — a fallback candidate's `distance_error_norm` in the response is still comparable to a matched candidate's, which is useful for a frontend that might want to show both.

`_rank_matched()` is essentially the old ranking function, just with the weighted-sum trimmed down to 4 terms instead of 6:
```python
def _rank_matched(
    matched: list[_PartialScore],
    distance_error_norms: list[float],
    mile_range_error_norms: list[float],
) -> list[ScoredCandidate]:
    if not matched:
        return []

    subtotals = [
        WEIGHT_ELEVATION_MISMATCH * partial.elevation_mismatch
        + WEIGHT_REPEATED_SEGMENT * partial.repeated_segment_ratio
        + WEIGHT_RESTROOM_CONFIDENCE * (1.0 - partial.restroom_confidence)
        for partial in matched
    ]

    # Similarity pass — same pattern as before: compute a provisional
    # order from the subtotal, then walk that order penalizing each
    # candidate by its overlap with every higher-ranked geometry so far.
    provisional_order = sorted(range(len(matched)), key=lambda i: subtotals[i])
    higher_ranked_geometries: list[tuple[RoutePoint, ...]] = []
    similarity_penalties = [0.0] * len(matched)
    for index in provisional_order:
        geometry = matched[index].candidate.geometry
        similarity_penalties[index] = similarity_penalty_for_candidate(geometry, higher_ranked_geometries)
        higher_ranked_geometries.append(geometry)

    scored_candidates = [
        ScoredCandidate(
            ...,
            composite_score=subtotals[index] + WEIGHT_SIMILARITY_PENALTY * similarity_penalties[index],
        )
        for index, partial in enumerate(matched)
    ]
    scored_candidates.sort(key=lambda sc: sc.composite_score)
    return scored_candidates
```

`_rank_fallback()` is new and much simpler — no similarity pass at all, because a fallback's ranking only cares about "closeness to the ask":
```python
def _rank_fallback(
    fallback: list[_PartialScore],
    distance_error_norms: list[float],
    mile_range_error_norms: list[float],
) -> list[ScoredCandidate]:
    scored_candidates = [
        ScoredCandidate(
            ...,
            similarity_penalty=0.0,
            composite_score=(
                WEIGHT_FALLBACK_DISTANCE_ERROR * distance_error_norms[index]
                + WEIGHT_FALLBACK_MILE_RANGE_ERROR * mile_range_error_norms[index]
            ),
        )
        for index, partial in enumerate(fallback)
    ]
    scored_candidates.sort(key=lambda sc: sc.composite_score)
    return scored_candidates
```

### `main.py`

**Before:**
```python
routing_provider = OpenRouteServiceProvider()


class RankedRouteResponse(BaseModel):
    geometry: tuple[RoutePoint, ...]
    distance_m: float
    elevation_gain_m: float
    restroom: RestroomInfo
    distance_error_m: float
    mile_range_error_m: float
    distance_error_norm: float
    mile_range_error_norm: float
    elevation_mismatch: float
    repeated_segment_ratio: float
    restroom_confidence: float
    similarity_penalty: float
    composite_score: float

...

    try:
        candidates = get_loop_candidates(
            provider,
            start,
            request.target_distance_m,
            request.count,
        )
    ...

    return [
        RankedRouteResponse(
            ...
            distance_error_m=scored.distance_error_m,
            ...
        )
        for scored in scored_candidates
    ]
```

**After:**
```python
routing_provider = OpenRouteServiceProvider()

# Internal candidate pool size for /routes/with-restroom, independent of
# the request's `count` (which now only controls how many *ranked*
# results are returned). 12 gives the scorer a meaningfully larger pool
# to choose matched/fallback candidates from than the old 3-5, while
# staying well within ORS's free-tier daily request budget (~2000/day —
# 12 calls/request supports ~150-200 requests/day).
GENERATE_CANDIDATE_COUNT = 12


class RankedRouteResponse(BaseModel):
    geometry: tuple[RoutePoint, ...]
    distance_m: float
    elevation_gain_m: float
    restroom: RestroomInfo
    matched: bool
    off_route_distance_m: float
    distance_error_m: float
    mile_range_error_m: float
    distance_error_norm: float
    mile_range_error_norm: float
    elevation_mismatch: float
    repeated_segment_ratio: float
    restroom_confidence: float
    similarity_penalty: float
    composite_score: float

...

    try:
        candidates = get_loop_candidates(
            provider,
            start,
            request.target_distance_m,
            GENERATE_CANDIDATE_COUNT,
        )
    ...

    return [
        RankedRouteResponse(
            ...
            matched=scored.matched,
            off_route_distance_m=scored.off_route_distance_m,
            distance_error_m=scored.distance_error_m,
            ...
        )
        for scored in scored_candidates[: request.count]
    ]
```

The two load-bearing diffs: `GENERATE_CANDIDATE_COUNT` replaces `request.count` in the `get_loop_candidates()` call (candidate generation is now decoupled from the requested result count), and `scored_candidates[: request.count]` is the new slice at the very end (this is the *only* place `request.count` still matters for this endpoint).

## Tests added/changed

### `tests/restrooms/test_scoring.py`

- **`test_score_and_rank_candidates_splits_matched_and_fallback`** (replaces the old `test_score_and_rank_candidates_sorts_by_composite_score`) — three candidates with hand-computed distance/range errors, one of which (`b`, distance_error=100) sits exactly at the hard-constraint boundary and is `matched=True`; the other two (`a` at distance_error=300, `c` at range_error=1000) are `matched=False` and land in the fallback pool, ranked by the `0.5/0.5` fallback formula with a documented stable-sort tiebreak.
- **`test_score_and_rank_candidates_ranks_matched_pool_by_renormalized_composite`** (new) — two matched candidates differing only in `elevation_mismatch`, hand-verifying the `3/7` weight and the similarity-penalty interaction (both candidates share an identical single-point geometry, so the second-ranked one picks up a full `1.0` similarity penalty — this test caught a wrong hand-computed expected value during development, which is exactly why the comment walks through the arithmetic).
- **`test_score_and_rank_candidates_backfills_with_fallback_when_understocked`** (new) — confirms `scoring.py` returns matched-then-fallback in order, with both pools present, when the matched pool alone wouldn't satisfy a hypothetical `count`.
- **`test_score_and_rank_candidates_off_route_distance_m_surfaces_match_field`** (new) — confirms `off_route_distance_m` on the response is a direct passthrough of `RestroomMatch.distance_to_route_m`, not a recomputation.

### `tests/test_routes_with_restroom.py`

- **`test_routes_with_restroom_success`** — updated to expect the two new response fields (`matched`, `off_route_distance_m`) in the field-set assertion and to check `matched is True`.
- **`test_routes_with_restroom_backfills_with_fallback_when_understocked`** (new) — uses a new `SingleSeedRoutingProvider` test double that returns one candidate shape for seed 1 and a different shape for all other seeds (1 matched candidate + 11 fallback candidates across the 12 internal calls), then requests `count=5` and confirms exactly 1 matched + 4 fallback-backfilled results come back, matched first.
- **`test_routes_with_restroom_count_slices_after_scoring`** (new) — same fixture, `count=1`, confirms only the top matched result is returned even though scoring internally produced 12 candidates — this is the test that directly proves the count-slicing lives in `main.py`.
- **`test_routes_with_restroom_422_only_on_zero_restroom_match_not_hard_constraint_failure`** (new) — a candidate that fails the distance hard constraint but still has an eligible restroom returns **200** with `matched=False`, not a 422. This directly tests the plan's explicit requirement that the 422 condition didn't change scope.
- **`test_routes_with_restroom_returns_422_when_no_match`** — unchanged, still confirms the true zero-restroom-match case still 422s.

## Live verification: Central Park scenario

Request sent to a running `uvicorn` instance:

```json
POST /routes/with-restroom
{
  "start_lat": 40.7829,
  "start_lon": -73.9654,
  "target_distance_m": 8000,
  "restroom_min_mile": 2.5,
  "restroom_max_mile": 3.5,
  "elevation_preference": "moderate",
  "count": 5
}
```

Response summary (geometry omitted for brevity — full geometries are present in the real response):

| # | distance_m | matched | distance_error_m | mile_range_error_m | off_route_distance_m | composite_score | restroom |
|---|---|---|---|---|---|---|---|
| 0 | 8079.8 | **True** | 79.8 | 144.7 | 113.9 | 0.00154 | Cherry Tree Park |
| 1 | 7906.8 | **True** | 93.2 | 0.0 | 40.3 | 0.23411 | 67th Street Library, NYPL |
| 2 | 7844.7 | False | 155.3 | 0.0 | 31.1 | 0.00185 | Central Park Chess & Checkers House |
| 3 | 7730.1 | False | 269.9 | 0.0 | 20.6 | 0.00466 | Carl Schurz Promenade |
| 4 | 7659.4 | False | 340.6 | 0.0 | 37.4 | 0.00639 | Central Park Chess & Checkers House |

What this shows:
- Both `matched=True` rows satisfy both hard constraints: row 0 has `distance_error_m=79.8 <= 100` and `mile_range_error_m=144.7 <= 500`; row 1 has `distance_error_m=93.2 <= 100` and `mile_range_error_m=0.0 <= 500`.
- Every `matched=False` row fails specifically on `distance_error_m` (155.3, 269.9, 340.6 — all > 100), while their `mile_range_error_m` is actually 0 (the restroom was exactly in range) — a clean demonstration that hard constraints are checked independently and a candidate needs to pass *both* to be `matched`.
- The matched pool is ranked ahead of the fallback pool (rows 0-1 before rows 2-4), confirming the concatenation order.
- `off_route_distance_m` is populated with plausible small values (20-114m), all under the 130m `RESTROOM_PROXIMITY_THRESHOLD_M` reachability cutoff, as expected since these are all real restroom matches.
- Row 1's `composite_score` (0.234) is much higher than row 0's (0.0015) despite both being matched — this is the renormalized composite doing its job: row 1 has `elevation_mismatch=0.5` and `similarity_penalty=0.10`, while row 0 has both at effectively 0, so row 0 ranks first within the matched pool.

This was run against the live backend with real Supabase restroom data and a real ORS API call — not a synthetic fixture — confirming the whole pipeline works end-to-end with production data shapes.

## Final verification status

- **mypy --strict**: clean — `Success: no issues found in 25 source files` (covers `app/` and `tests/`).
- **ruff check**: clean — `All checks passed!` across the whole backend.
- **pytest**: all passing — **53 passed**, 0 failed (1 pre-existing deprecation warning from `httpx`/`starlette.testclient`, unrelated to this change).
- **Live check**: confirmed above — `matched`/`off_route_distance_m` populate correctly and are internally consistent with `distance_error_m`/`mile_range_error_m` against the two hard-constraint thresholds.

## Files changed

- [backend/app/restrooms/scoring.py](file:///Users/oryagour/Desktop/run_route/backend/app/restrooms/scoring.py) — new constants, new dataclass fields, restructured `score_and_rank_candidates()` into `_rank_matched()` + `_rank_fallback()` helpers.
- [backend/app/main.py](file:///Users/oryagour/Desktop/run_route/backend/app/main.py) — new `GENERATE_CANDIDATE_COUNT` constant, new `RankedRouteResponse` fields, candidate generation decoupled from `request.count`, count-slicing added after scoring.
- [backend/tests/restrooms/test_scoring.py](file:///Users/oryagour/Desktop/run_route/backend/tests/restrooms/test_scoring.py) — replaced one outdated test, added 4 new tests covering matched/fallback split, renormalized composite, backfill ordering, and `off_route_distance_m` passthrough.
- [backend/tests/test_routes_with_restroom.py](file:///Users/oryagour/Desktop/run_route/backend/tests/test_routes_with_restroom.py) — updated the success-case assertions, added a `SingleSeedRoutingProvider` test double and 3 new tests covering backfill, count-slicing, and the 422-scope guarantee.

No changes were needed in `backend/app/restrooms/geo.py` or `backend/app/routing/candidates.py` — both were read-only reference points for this phase, exactly as the plan anticipated.
