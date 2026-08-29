# No-facility count-reliability benchmark — 2026-08-28T21:54:36

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.064 | 0.507s | 1.448s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.163s | 0.206s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.037 | 0.542s | 1.481s |
| ALL | 537 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.036 | 0.281s | 1.411s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.035 | 0.382s | 0.939s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.162s | 0.212s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.027 | 0.421s | 0.979s |
| ALL | 537 | 98.9% | 99.6% | 98.9% | 5.0 | 0 | 0 | 99.9% | 0.022 | 0.228s | 0.930s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.176s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.233s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.862s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.278s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.162s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.146s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.172s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.189s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.152s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.132s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.142s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.144s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.190s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.164s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.206s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.161s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.267s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.197s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.164s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.145s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.125s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.158s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.465s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.390s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.582s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.426s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.776s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.445s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.341s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.349s |
