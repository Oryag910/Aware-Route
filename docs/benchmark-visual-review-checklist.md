# Local-engine benchmark — visual review checklist

Purpose: `scripts/benchmark_suite.py` catches numeric defects (distance
error, turn counts, repeated-segment ratio, tiny-loop detection,
connectivity) automatically, but some quality problems are only obvious
by eye. This checklist is for manually reviewing the sample routes
exported to `benchmarks/local/route_*.geojson` (render them at
[geojson.io](https://geojson.io) or in QGIS/any GeoJSON viewer).

Each `route_*.geojson` is a `FeatureCollection` with one `LineString`
feature per candidate for that scenario, tagged with `scenario`,
`shape`, `target_miles`, `distance_m`, `candidate_index` in its
properties.

## How to review

1. Open each file in geojson.io (drag-and-drop) or a local viewer.
2. For each candidate line, walk it visually start to finish.
3. Check off any defect below that applies. Note the file name +
   candidate_index for anything flagged.
4. A route can be "numerically fine" (within ±100m, low turn count) and
   still fail this checklist — that gap is exactly what this review is
   for.

## Checklist

### Structural impossibilities (should NEVER occur — flag as a bug, not
### a quality nit, if seen)

- [ ] River crossing (route jumps across the Hudson, East River, or
      Harlem River without a bridge/tunnel path underfoot)
- [ ] Highway crossing (route crosses FDR Drive, West Side Highway, or
      similar limited-access road other than at a real pedestrian
      crossing)
- [ ] Ferry-only crossing (route implies travel over water with no land
      path)

These are excluded by construction (the walk graph has no edges over
water/highways), so if any of these appear it means a candidate's
geometry was corrupted or mis-stitched somewhere in generation — treat
as a P0 bug report, not a benchmark statistic.

### Route quality

- [ ] **Zigzags** — the route weaves back and forth across a short
      stretch (e.g. alternating between two nearly-parallel streets)
      instead of taking a direct corridor
- [ ] **Tiny distance-fixing loops** — a small out-and-back or loop
      spur that's obviously there to pad length rather than as a
      natural route feature (does it look intentional, or bolted on?)
- [ ] **Excessive turns** — more direction changes than a person would
      actually choose to run, even if each individual turn is legal
- [ ] **Repeated streets** — the same block is run more than once in a
      way that feels redundant rather than a deliberate out-and-back
- [ ] **Poor facility access** — for amenity-required scenarios, does
      the fountain actually sit *on* the runnable path (not just near
      it), and does reaching it require a detour that undermines the
      "in range" claim?
- [ ] **Valid-but-unpleasant** — technically correct (right distance,
      no crossings) but a route no one would want to run: e.g. entirely
      along a truck route, cuts through a parking lot, an awkward dead
      end and reverse, or crosses the same intersection repeatedly

### Shape fidelity

- [ ] `round` scenarios: does the loop actually look round/compact, not
      a thin sliver?
- [ ] `out_and_back` scenarios: is the outbound leg reasonably direct
      (not a loop pretending to be an out-and-back)?
- [ ] `mix` scenarios: does the result look like a reasonable route at
      all, even without a strict shape constraint?

### Hard-case specific checks

For scenarios drawn from the hard-case bank (graph corners, bridge
approaches, narrow peninsulas, tiny/huge targets — flagged `HARD -` in
the scenario name):

- [ ] Does the route stay sensible near the graph boundary (no
      shortcuts that imply leaving the walk graph)?
- [ ] For tiny targets (<1.5mi), is the route still a genuine shape
      rather than a degenerate there-and-immediately-back over one
      block?
- [ ] For huge targets (>10mi), does quality hold up, or does the
      route visibly fall apart (excessive backtracking, incoherent
      shape) once it's forced to cover a lot of ground in a small
      area?

## Recording findings

For each flagged issue, note:
- File name (`route_<scenario>.geojson`)
- `candidate_index`
- Which checklist item(s) failed
- One-line description of what's visually wrong

Roll these up alongside the automated report
(`benchmarks/local/report_<timestamp>.md`) before deciding the local
engine is ready to replace ORS for a given phase gate.
