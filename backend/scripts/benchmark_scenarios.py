"""Scenario bank for the local-graph engine benchmark suite.

Reuses the `Scenario` shape from `scripts/benchmark_routes.py` (name,
start_lat, start_lon, target_distance_miles) plus extra fields the local
engine understands (shape, elevation_preference, amenity requirement).
Elevation preference is carried as a parameter even though the local
engine does not score elevation yet -- it's here so scoring can pick it
up later without a scenario-bank rewrite.

Coordinates are real Manhattan points chosen to span:
  - Neighborhood: Upper / Midtown / Lower / East / West Manhattan
  - Central Park interior, waterfront promenades, near-river /
    graph-boundary starts (river edges, bridge approaches, island tips)
  - Distance: short (2-3mi) / medium (4-6mi) / long (7-10mi)
  - Shape: round / out_and_back / mix
  - Elevation preference: flat / moderate / hilly
  - Amenity requirement: on (fountain expected in range) / off
  - A subset of intentionally hard/sparse cases (graph corners, narrow
    peninsulas, highway-adjacent starts, tiny target distances)

Restrooms live in Supabase and are not available offline -- the amenity
dimension here is fountains only (see app.amenities.fountains). Restroom
coverage is validated separately at endpoint integration.
"""

from dataclasses import dataclass
from typing import Literal


Shape = Literal["round", "out_and_back", "mix"]
ElevationPreference = Literal["flat", "moderate", "hilly"]


@dataclass(frozen=True)
class Scenario:
    name: str
    start_lat: float
    start_lon: float
    target_distance_miles: float
    shape: Shape
    elevation_preference: ElevationPreference
    amenity_required: bool
    hard_case: bool = False


# ---------------------------------------------------------------------------
# Real Manhattan anchor points, grouped by area. Each anchor is combined
# with a spread of distance/shape/elevation/amenity axes below so the
# bank covers the full cross product without hand-writing 200+ literals.
# ---------------------------------------------------------------------------

_UPPER_MANHATTAN: tuple[tuple[str, float, float], ...] = (
    ("Inwood Hill Park", 40.8677, -73.9212),
    ("Inwood - Dyckman St", 40.8654, -73.9280),
    ("Washington Heights - Bennett Park", 40.8515, -73.9375),
    ("Washington Heights - Fort Tryon", 40.8598, -73.9317),
    ("Harlem - Marcus Garvey Park", 40.8036, -73.9418),
    ("Harlem - 125th St", 40.8116, -73.9465),
    ("Morningside Heights", 40.8065, -73.9629),
    ("Hamilton Heights", 40.8262, -73.9476),
    ("Manhattanville", 40.8168, -73.9557),
    ("East Harlem", 40.7957, -73.9389),
)

_CENTRAL_PARK: tuple[tuple[str, float, float], ...] = (
    ("Central Park - Reservoir", 40.7829, -73.9654),
    ("Central Park - North Woods", 40.7969, -73.9495),
    ("Central Park - Great Lawn", 40.7813, -73.9648),
    ("Central Park - Sheep Meadow", 40.7712, -73.9755),
    ("Central Park - Bethesda Terrace", 40.7739, -73.9707),
    ("Central Park - Harlem Meer", 40.7968, -73.9526),
    ("Central Park - South entrance", 40.7660, -73.9760),
)

_UPPER_WEST_SIDE: tuple[tuple[str, float, float], ...] = (
    ("Upper West Side - 86th", 40.7870, -73.9754),
    ("Upper West Side - Lincoln Sq", 40.7736, -73.9832),
    ("Riverside Park - 96th", 40.7935, -73.9764),
    ("Riverside Park - 116th", 40.8034, -73.9668),
)

_UPPER_EAST_SIDE: tuple[tuple[str, float, float], ...] = (
    ("Upper East Side - Yorkville", 40.7754, -73.9482),
    ("Upper East Side - Lenox Hill", 40.7663, -73.9598),
    ("Carl Schurz Park (East River)", 40.7757, -73.9433),
    ("Roosevelt Island footbridge approach", 40.7614, -73.9509),
)

_MIDTOWN: tuple[tuple[str, float, float], ...] = (
    ("Midtown - Times Sq area", 40.7549, -73.9840),
    ("Midtown East - Grand Central", 40.7527, -73.9772),
    ("Hell's Kitchen", 40.7638, -73.9918),
    ("Midtown - Bryant Park", 40.7536, -73.9832),
    ("Turtle Bay (East River)", 40.7517, -73.9648),
)

_CHELSEA_FLATIRON: tuple[tuple[str, float, float], ...] = (
    ("Chelsea", 40.7465, -74.0014),
    ("Flatiron", 40.7411, -73.9897),
    ("Chelsea - Hudson River Park", 40.7484, -74.0079),
    ("Gramercy Park", 40.7378, -73.9857),
)

_LOWER_MANHATTAN: tuple[tuple[str, float, float], ...] = (
    ("Lower East Side", 40.7168, -73.9861),
    ("East Village", 40.7265, -73.9815),
    ("Greenwich Village", 40.7336, -74.0027),
    ("SoHo", 40.7233, -74.0030),
    ("Tribeca", 40.7163, -74.0086),
    ("Financial District", 40.7075, -74.0113),
    ("Battery Park", 40.7033, -74.0170),
    ("South Street Seaport (East River)", 40.7075, -74.0021),
    ("Chinatown", 40.7157, -73.9970),
)

_WATERFRONT_AND_BOUNDARY: tuple[tuple[str, float, float], ...] = (
    ("Hudson River Greenway - 34th St pier", 40.7557, -74.0025),
    ("Hudson River Greenway - Battery tip", 40.7009, -74.0176),
    ("East River Esplanade - 60th St", 40.7614, -73.9558),
    ("East River Esplanade - Stuyvesant Cove", 40.7326, -73.9740),
    ("Harlem River - Swindler Cove", 40.8330, -73.9337),
    ("Harlem River - 155th St bridge approach", 40.8302, -73.9310),
    ("George Washington Bridge approach", 40.8517, -73.9435),
    ("Brooklyn Bridge approach (Manhattan side)", 40.7128, -74.0026),
    ("Williamsburg Bridge approach (Manhattan side)", 40.7145, -73.9764),
    ("Manhattan Bridge approach (Manhattan side)", 40.7135, -73.9903),
    ("FDR Drive edge - 96th St", 40.7838, -73.9445),
    ("West Side Highway edge - 57th St", 40.7706, -73.9970),
    ("Randall's Island footbridge approach", 40.7906, -73.9270),
    ("Battery Park City - northern tip", 40.7191, -74.0151),
    ("Inwood - Harlem River bend", 40.8720, -73.9198),
)


def _anchors() -> list[tuple[str, float, float]]:
    return [
        *_UPPER_MANHATTAN,
        *_CENTRAL_PARK,
        *_UPPER_WEST_SIDE,
        *_UPPER_EAST_SIDE,
        *_MIDTOWN,
        *_CHELSEA_FLATIRON,
        *_LOWER_MANHATTAN,
        *_WATERFRONT_AND_BOUNDARY,
    ]


_DISTANCE_AXIS: tuple[float, ...] = (2.5, 5.0, 8.0)
_SHAPE_AXIS: tuple[Shape, ...] = ("round", "out_and_back", "mix")
_ELEVATION_AXIS: tuple[ElevationPreference, ...] = ("flat", "moderate", "hilly")


def _build_grid_scenarios() -> list[Scenario]:
    """Cross the anchor list with distance/shape so every area is hit at
    every distance and every shape at least once, cycling elevation
    preference and amenity requirement deterministically for spread
    without a full N^4 explosion."""
    anchors = _anchors()
    scenarios: list[Scenario] = []

    combo_index = 0

    for anchor_name, lat, lon in anchors:
        for distance_miles in _DISTANCE_AXIS:
            for shape in _SHAPE_AXIS:
                elevation = _ELEVATION_AXIS[combo_index % len(_ELEVATION_AXIS)]
                amenity_required = combo_index % 2 == 0
                combo_index += 1

                scenarios.append(
                    Scenario(
                        name=(
                            f"{anchor_name} | {distance_miles}mi | {shape} | "
                            f"{elevation} | "
                            f"{'amenity' if amenity_required else 'no-amenity'}"
                        ),
                        start_lat=lat,
                        start_lon=lon,
                        target_distance_miles=distance_miles,
                        shape=shape,
                        elevation_preference=elevation,
                        amenity_required=amenity_required,
                    )
                )

    return scenarios


# ---------------------------------------------------------------------------
# Intentionally hard / sparse cases -- graph corners, tiny targets, huge
# targets, narrow peninsulas, bridge-approach starts far from amenities.
# ---------------------------------------------------------------------------

_HARD_CASES: tuple[Scenario, ...] = (
    Scenario(
        "HARD - Inwood tip, tiny target",
        40.8747,
        -73.9209,
        1.0,
        "round",
        "hilly",
        False,
        hard_case=True,
    ),
    Scenario(
        "HARD - Inwood tip, huge target",
        40.8747,
        -73.9209,
        12.0,
        "out_and_back",
        "hilly",
        False,
        hard_case=True,
    ),
    Scenario(
        "HARD - Battery Park tip, tiny target",
        40.7009,
        -74.0176,
        1.0,
        "mix",
        "flat",
        True,
        hard_case=True,
    ),
    Scenario(
        "HARD - Battery Park tip, huge target",
        40.7009,
        -74.0176,
        12.0,
        "round",
        "flat",
        False,
        hard_case=True,
    ),
    Scenario(
        "HARD - Roosevelt Island footbridge, narrow strip",
        40.7614,
        -73.9509,
        6.0,
        "round",
        "flat",
        True,
        hard_case=True,
    ),
    Scenario(
        "HARD - GWB approach, steep + amenity-sparse",
        40.8517,
        -73.9435,
        7.0,
        "out_and_back",
        "hilly",
        True,
        hard_case=True,
    ),
    Scenario(
        "HARD - Randall's Island footbridge, edge of graph",
        40.7906,
        -73.9270,
        5.0,
        "mix",
        "moderate",
        True,
        hard_case=True,
    ),
    Scenario(
        "HARD - Harlem River bend, tiny target",
        40.8720,
        -73.9198,
        1.5,
        "round",
        "hilly",
        False,
        hard_case=True,
    ),
    Scenario(
        "HARD - FDR Drive edge, amenity required",
        40.7838,
        -73.9445,
        4.0,
        "out_and_back",
        "flat",
        True,
        hard_case=True,
    ),
    Scenario(
        "HARD - West Side Highway edge, huge target",
        40.7706,
        -73.9970,
        10.0,
        "mix",
        "flat",
        False,
        hard_case=True,
    ),
    Scenario(
        "HARD - Brooklyn Bridge approach, tiny + amenity",
        40.7128,
        -74.0026,
        1.0,
        "round",
        "flat",
        True,
        hard_case=True,
    ),
    Scenario(
        "HARD - Central Park Reservoir, tiny loop",
        40.7829,
        -73.9654,
        0.75,
        "round",
        "flat",
        False,
        hard_case=True,
    ),
    Scenario(
        "HARD - Battery Park City tip, out_and_back huge",
        40.7191,
        -74.0151,
        11.0,
        "out_and_back",
        "moderate",
        False,
        hard_case=True,
    ),
    Scenario(
        "HARD - Swindler Cove, amenity-sparse hilly",
        40.8330,
        -73.9337,
        6.0,
        "mix",
        "hilly",
        True,
        hard_case=True,
    ),
    Scenario(
        "HARD - Manhattan Bridge approach, tiny target",
        40.7135,
        -73.9903,
        0.75,
        "out_and_back",
        "flat",
        False,
        hard_case=True,
    ),
)


SCENARIOS: tuple[Scenario, ...] = tuple(_build_grid_scenarios()) + _HARD_CASES


if __name__ == "__main__":
    print(f"Total scenarios: {len(SCENARIOS)}")
    hard = [s for s in SCENARIOS if s.hard_case]
    print(f"Hard cases: {len(hard)}")
    by_shape: dict[str, int] = {}
    for s in SCENARIOS:
        by_shape[s.shape] = by_shape.get(s.shape, 0) + 1
    print(f"By shape: {by_shape}")
