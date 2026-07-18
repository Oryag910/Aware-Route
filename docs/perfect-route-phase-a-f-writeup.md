# Implementation write-up: perfect-route roadmap, phases A–F

Executed 2026-07-17/18 against `docs/perfect-route-roadmap.md`. All six phases are
implemented, tested (183 backend tests, mypy --strict across 50 files, ruff, frontend
tsc+vite all clean), and committed individually. This documents what was built, what
went wrong along the way (the interesting part), and the decisions that were made on
your behalf — flagged so you can revisit any of them.

## Phase A — distance accuracy + latency (the hard one)

**Result: both gates passed.** Match rate 53% → **76.5%** (13/17 benchmark scenarios
with ≥1 matched route; gate was ≥75%). Median response 16.7s → **~7s** (gate <8s).
Median best distance error 336m → **41m**. Historic trouble spots converted: Battery
Park 1949m → 8.2m matched, Tribeca 1130m → 14.7m matched.

It took five benchmark runs and three real bugs to get there — each found by
measurement, not guesswork:

1. **Repair aborted one round before converging.** Reshaping a loop into an
   out-and-back always passes through a much-worse intermediate attempt before the
   radial is resized; the no-improvement check compared against the *original*
   candidate and killed every trajectory at the dip. Fixed by judging progress
   against the previous attempt on the same trajectory.
2. **Repair fixed distance by breaking the restroom.** Repaired routes lost their
   restroom placement (dropped from scoring or buried in the fallback pool on range
   error). Fixed twice over: every repair target pins a restroom as a via waypoint,
   chosen *predictively* (straight-line closeness to the band midpoint — what
   predicts the marker on the reshaped route), and repair places it on the outbound
   or return leg depending on which lands in the requested band. Originals are kept
   alongside repaired versions so repair can only add options.
3. **The benchmark was measuring quota contention, not the algorithm.** Parallel
   bursts at 20-25s spacing sat on the ORS 40/min ceiling; generation calls fired
   first and succeeded, repair calls landed last and silently ate the 429s — which
   is why results were unstable and in-process replays kept outperforming the
   server. Fixed with a 429 retry in ors.py and honest benchmark pacing (35s).

Also in A: parallelized generation/repair (thread pool, submission-order results,
budgets pre-assigned most-promising-first), a rescue pass repairing up-to-50%-off
candidates when nothing matched, seed-failure tolerance in blind generation, and
timestamped benchmark JSON under `backend/benchmarks/`.

**Remaining known misses** (all logged in run-5 JSON): Central Park Reservoir
(117.9m over by 18m), Harlem (135.4m), Lower East Side (128.2m — never improves;
street-grid quantization seems to defeat ±28m nudging), Battery Park-longer (336m).
These are the target list for any future repair tuning.

## Phase B — amenity correctness

`hours.py` parses restroom hours (AM/PM ranges, 24-hour, dawn-to-dusk approximated
6am-8pm); requests take optional `run_time` (default now) and restrooms *confidently
closed* at run time are excluded before anything uses them. Unknown/unparseable
hours never exclude — the product must not claim "closed" without knowing. Off-route
reachability became a scored factor and the UI shows "18 m off-route" vs "~120 m
detour required".

## Phase C — flow and surfaces (the two-lever phase, zero extra ORS calls)

ORS `extra_info` (surface/waytype/steepness) piggybacks on existing calls; a one-time
Overpass ingest (7,663 Manhattan traffic signals + 35,551 crossings, committed to
`backend/data/interruptions.json`) powers per-route signal/crossing counts, longest
uninterrupted stretch, and signals/km — interruption density is now 2/10 of the
matched composite. Crossings are reported but deliberately unscored (OSM crossing
tagging is too noisy). Park/pedestrian share and a stairs flag come from waytype
segments.

## Phase D — shape + explanations

Sharp-turn/U-turn counts (15m leg accumulation to defeat vertex jitter) and
compactness (isoperimetric quotient). Deliberately *not* composite-weighted — 
repeated-segment ratio already punishes the dominant failure (out-and-backs), and
presets weight turns where it matters. The frontend cards dropped every raw
normalized float (`0.714`-style) for facts: distance, restroom position + offset,
smoothed elevation gain, signals/crossings, longest uninterrupted stretch, park
share, turns, stairs — plus a one-line tradeoff vs Route 1 ("1 more traffic signal,
but 12% more park paths").

## Phase E — elevation profiles

Per-point elevations (stored since the first ORS session, never analyzed) get a
rolling-median smoothing that directly kills the known DEM noise (spot-check: 130m
smoothed vs 194m raw on a Central Park loop; the historic case was 342m "ascent" on
flat Midtown). Sustained-climb detection (≥2% over ≥200m, 60m dip tolerance) feeds
profile floors: "flat" preference with a real climb, or "hilly" preference with only
noise-sum gain, both floor mismatch at 0.5. Cards show "Longest climb: 800 m at 4.2%".

## Phase F — workout presets + archetypes

`workout_type` (easy/tempo/long/hills/intervals) selects a weight profile over eight
factors; the default profile reproduces the old composite exactly (no behavior change
without opt-in). Integer weight parts, normalized: default 3,2,2,1,1,1,0,0 over
(elevation, repeats, interruptions, similarity, confidence, off-route, turns,
street-share); tempo pushes interruptions/turns, intervals un-penalizes repeats,
long boosts amenity confidence, hills makes elevation dominant. Ranked results get
archetype badges — best overall / smoothest / most scenic — with a 0.7
geometry-overlap cap so near-duplicates can't hold two labels.

## Post-F fix from the live spot-check

ORS round_trip occasionally returns absurd loops (observed: 29km and 723km for a
9.7km ask) that displaced real candidates in the fallback ranking. Blind candidates
beyond 3× target are now dropped before scoring.

## Decisions made on your behalf (revisit freely)

- **Weight choices**: interruption density at 2/10; off-route at 1/8→1/10 through
  the phases; the preset tables in `WEIGHT_PROFILES`; turn-density ceiling 6/km;
  signals/km ceiling 8. All constants, all commented, none benchmarked individually.
- **Crossings unscored**, dawn-to-dusk = 6am-8pm, shape metrics unscored by default.
- **Overpass data as a committed file** rather than a Supabase table (no DDL access;
  also rate-limit-proof and reviewable).
- **Repair breadth over depth** (3 calls/candidate) — this one *was* benchmarked.
- Roadmap flagged a product-spec §12 conflict (traffic-light optimization was a
  non-goal): Phase C ships interruption *scoring*, not signal-avoiding *generation*.
  §12 still needs an amendment if you're keeping this.

## Costs and constraints

- Per-request ORS ceiling unchanged at 20 calls; phases B–F added zero.
- Benchmarking burned ~1,900 ORS calls on 2026-07-17 (five full runs + partials +
  diagnostics); the quota day rolled over before the final spot-checks.
- The full benchmark now takes ~12 min (35s pacing) — that's the price of results
  that measure the algorithm instead of the rate limiter.

## What a next session could pick up

1. The four remaining benchmark misses (LES's quantization miss suggests trying
   *alternate anchors* rather than finer nudges).
2. Amend product-spec §12 (traffic lights) to match reality.
3. Styling — the frontend now says smart things but still looks like default HTML.
4. A fresh full benchmark run to confirm B–F didn't regress Phase A's gates (they
   add no ORS calls and don't touch generation/repair, but measured is better than
   argued — ~340 calls).
