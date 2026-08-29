# No-facility count-reliability benchmark — 2026-08-28T22:01:39

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.043 | 0.369s | 0.905s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.161s | 0.212s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.034 | 0.412s | 0.975s |
| ALL | 537 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.027 | 0.231s | 0.896s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.035 | 0.394s | 0.934s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.161s | 0.207s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.027 | 0.428s | 0.996s |
| ALL | 537 | 98.9% | 99.6% | 98.9% | 5.0 | 0 | 0 | 99.9% | 0.022 | 0.226s | 0.916s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.179s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.214s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.316s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.305s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.148s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.148s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.182s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.188s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.135s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.135s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.144s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.146s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.192s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.166s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.162s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.164s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.216s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.229s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.149s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.149s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.158s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.128s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.382s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.442s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.432s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.448s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.449s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.455s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.396s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.375s |
