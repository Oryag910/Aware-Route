# No-facility count-reliability benchmark — 2026-08-28T20:04:38

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.043 | 0.373s | 0.977s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.162s | 0.210s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.034 | 0.419s | 1.012s |
| ALL | 537 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.027 | 0.236s | 0.928s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.035 | 0.399s | 0.994s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.167s | 0.209s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.027 | 0.429s | 1.013s |
| ALL | 537 | 98.9% | 99.6% | 98.9% | 5.0 | 0 | 0 | 99.9% | 0.022 | 0.227s | 0.940s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.177s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.199s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.275s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.351s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.174s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.146s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.161s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.162s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.162s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.134s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.142s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.145s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.163s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.196s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.160s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.189s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.193s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.224s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.172s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.147s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.127s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.185s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.376s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.379s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.427s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.430s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.476s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.450s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.400s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.393s |
