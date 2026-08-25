# No-facility count-reliability benchmark — 2026-08-25T18:33:54

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 99.4% | 99.4% | 3.0 | 0 | 0 | 99.8% | 0.043 | 0.503s | 1.263s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.179s | 0.230s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.034 | 0.550s | 1.355s |
| ALL | 537 | 100.0% | 99.8% | 99.8% | 3.0 | 0 | 0 | 99.9% | 0.027 | 0.272s | 1.253s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 98.3% | 98.9% | 97.8% | 5.0 | 0 | 0 | 99.8% | 0.035 | 0.513s | 1.274s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.177s | 0.235s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.027 | 0.549s | 1.369s |
| ALL | 537 | 98.9% | 99.4% | 98.7% | 5.0 | 0 | 0 | 99.9% | 0.022 | 0.266s | 1.251s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.199s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.206s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.762s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.775s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.195s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.166s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.190s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.206s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.184s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.151s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.155s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.155s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.176s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.210s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.178s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.207s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.216s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.247s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.192s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.166s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.132s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.146s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.487s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.494s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.567s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.568s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.632s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.559s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.882s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.894s |
