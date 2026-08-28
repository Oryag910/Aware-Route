# No-facility count-reliability benchmark — 2026-08-28T12:14:12

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 99.4% | 99.4% | 3.0 | 0 | 0 | 99.8% | 0.043 | 0.366s | 0.921s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.159s | 0.204s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.034 | 0.416s | 0.965s |
| ALL | 537 | 100.0% | 99.8% | 99.8% | 3.0 | 0 | 0 | 99.9% | 0.027 | 0.232s | 0.903s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 98.3% | 98.9% | 97.8% | 5.0 | 0 | 0 | 99.8% | 0.035 | 0.383s | 0.938s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.163s | 0.198s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.027 | 0.418s | 0.989s |
| ALL | 537 | 98.9% | 99.4% | 98.7% | 5.0 | 0 | 0 | 99.9% | 0.022 | 0.227s | 0.901s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.209s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.185s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.284s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.264s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.145s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.146s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.189s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.161s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.134s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.133s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.146s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.178s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.161s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.166s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.162s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.164s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.231s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.196s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.144s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.145s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.127s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.127s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.382s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.411s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.418s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.470s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.445s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.509s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.352s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.366s |
