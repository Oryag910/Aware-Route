# No-facility count-reliability benchmark — 2026-08-25T17:56:16

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | median returned | returned==1 | returned==2 | within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 3.0 | 0 | 0 | 87.4% | 0.049 | 0.340s | 0.711s |
| out_and_back | 179 | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.162s | 0.203s |
| mix | 178 | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.027 | 0.369s | 0.738s |
| ALL | 537 | 100.0% | 3.0 | 0 | 0 | 95.8% | 0.027 | 0.217s | 0.669s |

## requested count = 5

| shape | scenarios | exact-count % | median returned | returned==1 | returned==2 | within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 98.3% | 5.0 | 0 | 0 | 76.1% | 0.044 | 0.349s | 0.676s |
| out_and_back | 179 | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.166s | 0.210s |
| mix | 178 | 100.0% | 5.0 | 0 | 0 | 99.6% | 0.021 | 0.376s | 0.710s |
| ALL | 537 | 98.9% | 5.0 | 0 | 0 | 91.8% | 0.022 | 0.224s | 0.676s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.178s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.184s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 0.800s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 0.851s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.171s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.143s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.159s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.152s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.128s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.174s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.143s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.145s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.162s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.201s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.154s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.185s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.191s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.221s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.170s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.142s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.127s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.127s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.310s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.314s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.363s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.366s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.348s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.377s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 3.365s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 3.467s |
