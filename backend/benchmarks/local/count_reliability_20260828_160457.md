# No-facility count-reliability benchmark — 2026-08-28T16:04:57

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.043 | 0.367s | 0.901s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.159s | 0.207s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.034 | 0.416s | 0.987s |
| ALL | 537 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.027 | 0.234s | 0.900s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.035 | 0.390s | 0.934s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.163s | 0.199s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.027 | 0.416s | 0.992s |
| ALL | 537 | 98.9% | 99.6% | 98.9% | 5.0 | 0 | 0 | 99.9% | 0.022 | 0.227s | 0.897s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.207s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.181s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.321s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.303s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.150s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.147s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.187s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.162s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.132s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.131s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.143s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.179s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.162s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.165s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.161s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.165s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.238s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.198s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.146s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.148s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.126s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.125s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.383s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.418s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.430s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.487s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.446s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.502s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.365s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.343s |
