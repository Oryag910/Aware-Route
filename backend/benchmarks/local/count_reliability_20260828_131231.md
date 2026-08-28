# No-facility count-reliability benchmark — 2026-08-28T13:12:31

Fills the blind spot in the historical 537-scenario suite (scripts/benchmark_suite.py), whose only success criterion was ">=1 valid route" -- it never asserted the RETURNED count matched the REQUESTED count. This exercises the real product code path (`app.facilities.orchestration.plan_routes` with `facility_requirements=[]`), not `generate_candidates` directly, which bypasses the no-facility overcomplete-pool policy entirely (see `app.facilities.orchestration.natural_match_pool`).

## requested count = 3

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.062 | 0.526s | 1.553s |
| out_and_back | 179 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.008 | 0.164s | 0.206s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.040 | 0.574s | 1.599s |
| ALL | 537 | 100.0% | 100.0% | 100.0% | 3.0 | 0 | 0 | 100.0% | 0.036 | 0.282s | 1.506s |

## requested count = 5

| shape | scenarios | exact-count % | all-returned-within-100m % | exact-count AND all-within-100m % | median returned | returned==1 | returned==2 | candidate within-100m rate | median overlap | median latency | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round | 180 | 100.0% | 90.0% | 90.0% | 5.0 | 0 | 0 | 97.6% | 0.061 | 0.514s | 1.541s |
| out_and_back | 179 | 98.3% | 99.4% | 98.3% | 5.0 | 0 | 0 | 99.9% | 0.009 | 0.167s | 0.204s |
| mix | 178 | 100.0% | 100.0% | 100.0% | 5.0 | 0 | 0 | 100.0% | 0.031 | 0.569s | 1.588s |
| ALL | 537 | 99.4% | 96.5% | 96.1% | 5.0 | 0 | 0 | 99.1% | 0.032 | 0.280s | 1.464s |

## Known hard-case detail

| scenario | shape | requested | returned | time |
|---|---|---|---|---|
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 3 | 3 | 0.177s |
| HARD - Battery Park City tip, out_and_back huge | out_and_back | 5 | 5 | 0.183s |
| HARD - Battery Park tip, huge target | round | 3 | 3 | 1.983s |
| HARD - Battery Park tip, huge target | round | 5 | 5 | 2.165s |
| HARD - Battery Park tip, tiny target | mix | 3 | 3 | 0.164s |
| HARD - Battery Park tip, tiny target | mix | 5 | 5 | 0.164s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 3 | 3 | 0.172s |
| HARD - Brooklyn Bridge approach, tiny + amenity | round | 5 | 5 | 0.174s |
| HARD - Central Park Reservoir, tiny loop | round | 3 | 3 | 0.169s |
| HARD - Central Park Reservoir, tiny loop | round | 5 | 5 | 0.181s |
| HARD - FDR Drive edge, amenity required | out_and_back | 3 | 3 | 0.143s |
| HARD - FDR Drive edge, amenity required | out_and_back | 5 | 5 | 0.145s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 3 | 3 | 0.167s |
| HARD - GWB approach, steep + amenity-sparse | out_and_back | 5 | 5 | 0.201s |
| HARD - Harlem River bend, tiny target | round | 3 | 3 | 0.181s |
| HARD - Harlem River bend, tiny target | round | 5 | 5 | 0.208s |
| HARD - Inwood tip, huge target | out_and_back | 3 | 3 | 0.192s |
| HARD - Inwood tip, huge target | out_and_back | 5 | 5 | 0.277s |
| HARD - Inwood tip, tiny target | round | 3 | 3 | 0.172s |
| HARD - Inwood tip, tiny target | round | 5 | 5 | 0.175s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 3 | 3 | 0.129s |
| HARD - Manhattan Bridge approach, tiny target | out_and_back | 5 | 5 | 0.127s |
| HARD - Randall's Island footbridge, edge of graph | mix | 3 | 3 | 0.495s |
| HARD - Randall's Island footbridge, edge of graph | mix | 5 | 5 | 0.499s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 3 | 3 | 0.586s |
| HARD - Roosevelt Island footbridge, narrow strip | round | 5 | 5 | 0.588s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 3 | 3 | 0.883s |
| HARD - Swindler Cove, amenity-sparse hilly | mix | 5 | 5 | 0.778s |
| HARD - West Side Highway edge, huge target | mix | 3 | 3 | 1.355s |
| HARD - West Side Highway edge, huge target | mix | 5 | 5 | 1.390s |
