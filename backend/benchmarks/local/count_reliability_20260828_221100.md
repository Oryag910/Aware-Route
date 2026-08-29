# No-facility count-reliability benchmark — 2026-08-28T22:11:00

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.064 | 0.505s | 1.481s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.163s | 0.204s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.037 | 0.539s | 1.521s |
| ALL | 537 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.036 | 0.278s | 1.430s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 93.3% | 93.3% | 5.0 | 0 | 0 | 98.4% | 0.060 | 0.501s | 1.518s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.165s | 0.213s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.030 | 0.552s | 1.537s |
| ALL | 537 | 99.4% | 97.6% | 97.2% | 5.0 | 0 | 0 | 99.4% | 0.031 | 0.270s | 1.447s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.191s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.213s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.845s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.811s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.163s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.173s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.206s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.174s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.153s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.151s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.142s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.175s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.162s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.166s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.178s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.178s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.215s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.198s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.168s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.199s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.165s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.128s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.468s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.500s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.591s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.621s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.786s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.790s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.318s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.332s |
