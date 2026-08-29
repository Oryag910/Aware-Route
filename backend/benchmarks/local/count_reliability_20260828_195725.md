# No-facility count-reliability benchmark — 2026-08-28T19:57:25

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.062 | 0.538s | 1.539s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.162s | 0.207s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.040 | 0.590s | 1.574s |
| ALL | 537 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.037 | 0.301s | 1.522s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 93.9% | 93.9% | 5.0 | 0 | 0 | 98.8% | 0.061 | 0.544s | 1.604s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.164s | 0.205s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.030 | 0.564s | 1.637s |
| ALL | 537 | 99.4% | 97.8% | 97.4% | 5.0 | 0 | 0 | 99.6% | 0.031 | 0.291s | 1.533s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.180s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.200s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.846s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 1.783s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.168s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.163s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.184s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.203s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.189s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.159s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.145s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.146s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.194s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.167s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.209s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.183s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.224s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.227s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.167s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.201s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.126s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.158s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.521s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.521s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.625s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.598s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.932s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.896s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.447s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.409s |
