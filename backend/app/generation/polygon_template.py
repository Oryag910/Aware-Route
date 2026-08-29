"""Pure geometry for the polygon-loop V2 round-route generator (PR #15).

Generates synthetic rectangle-loop anchor coordinates around a start
point -- no graph awareness here. `polygon_loop.py` snaps these
synthetic anchors onto the walk graph and stitches the actual route.

Anchor layout (start = A, walked A -> B -> C -> D -> A):

        B -------- C
        |          |
        |          |
 START/A ---------- D

`rotation_deg` is the compass bearing of the A->B leg; `direction`
picks whether the walk turns clockwise or counterclockwise at each
corner. Anchors are placed with `app.routing.geometry.destination_point`
-- the same spherical bearing/distance walk already used elsewhere in
this codebase (turnarounds, out_and_back) -- rather than a fresh
projection, so no new coordinate math is introduced.
"""

from dataclasses import dataclass
from typing import Literal

from app.routing.geometry import destination_point
from app.routing.provider import Coordinate


Direction = Literal["cw", "ccw"]


@dataclass(frozen=True)
class LoopTemplate:
    rotation_deg: float
    aspect_ratio: float  # height / width
    direction: Direction


# A small, fixed, deterministic set of templates spanning rotation,
# clockwise/counterclockwise traversal, and aspect ratio. Rotations are
# offset from the cardinal compass points (0/90/180/270) so templates
# don't systematically align with a north/south/east/west street grid
# and all land on the same handful of headings. Aspect ratios follow
# the ~0.7-1.4 family (roughly square to moderately elongated
# rectangle); both directions are exercised across the set so no
# single traversal sense dominates.
TEMPLATES: tuple[LoopTemplate, ...] = (
    LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw"),
    LoopTemplate(rotation_deg=45.0, aspect_ratio=0.7, direction="ccw"),
    LoopTemplate(rotation_deg=90.0, aspect_ratio=1.4, direction="cw"),
    LoopTemplate(rotation_deg=135.0, aspect_ratio=1.0, direction="ccw"),
    LoopTemplate(rotation_deg=180.0, aspect_ratio=0.85, direction="cw"),
    LoopTemplate(rotation_deg=225.0, aspect_ratio=1.2, direction="ccw"),
    LoopTemplate(rotation_deg=270.0, aspect_ratio=1.0, direction="cw"),
    LoopTemplate(rotation_deg=315.0, aspect_ratio=0.7, direction="ccw"),
    LoopTemplate(rotation_deg=22.5, aspect_ratio=1.4, direction="ccw"),
    LoopTemplate(rotation_deg=157.5, aspect_ratio=0.85, direction="cw"),
)


def template_anchors(
    start: Coordinate,
    target_distance_m: float,
    template: LoopTemplate,
    scale: float = 1.0,
) -> tuple[Coordinate, Coordinate, Coordinate]:
    """The (B, C, D) synthetic anchor coordinates for `template`.

    The rectangle's perimeter is set to `target_distance_m * scale`
    (2*(width+height) ~= perimeter); `scale` is the caller's lever for
    distance tuning -- shrink it to pull the loop's graph-routed
    distance down, grow it to push distance up. `aspect_ratio` is
    height/width, so `width = perimeter / (2*(1+aspect_ratio))` and
    `height = aspect_ratio * width`.

    Each corner is placed by walking a bearing + distance from the
    previous one (A=start is implicit and not returned -- callers
    already have it). `direction="cw"` turns +90 degrees at each
    corner, `"ccw"` turns -90 degrees, so the same aspect ratio and
    rotation can walk the rectangle either way around.
    """
    perimeter_m = target_distance_m * scale
    width_m = perimeter_m / (2.0 * (1.0 + template.aspect_ratio))
    height_m = template.aspect_ratio * width_m

    turn = 90.0 if template.direction == "cw" else -90.0
    bearing_ab = template.rotation_deg
    bearing_bc = bearing_ab + turn
    bearing_cd = bearing_bc + turn

    b = destination_point(start, bearing_ab, height_m)
    c = destination_point(b, bearing_bc, width_m)
    d = destination_point(c, bearing_cd, height_m)

    return (b, c, d)


def axis_scaled_template_anchors(
    start: Coordinate,
    target_distance_m: float,
    template: LoopTemplate,
    height_scale: float,
    width_scale: float,
) -> tuple[Coordinate, Coordinate, Coordinate]:
    """Generalizes `template_anchors` to scale the "height" legs
    (A->B and C->D) and the "width" leg (B->C) INDEPENDENTLY, instead
    of by one shared `scale`. `template_anchors(..., scale=s)` is the
    special case `height_scale=width_scale=s` -- this function computes
    the same base `width_m`/`height_m` split from `aspect_ratio` and
    then applies each axis's own multiplier.

    Used by `polygon_loop.py`'s bounded per-axis refinement (see
    `_tune_template`'s docstring): once a template's ordinary
    uniform-scale search plateaus outside tolerance, a topology-
    constrained direction (e.g. a peninsula's water boundary) can be
    given a SMALLER share of the template's distance budget while the
    other axis is given a LARGER share, instead of forcing both to the
    same compromise scale that overshoots one and undershoots the
    other. Only two fixed, bounded ratio pairs are ever tried (see
    `AXIS_REFINEMENT_RATIOS`) -- this is not a search function itself.
    """
    base_width_m = target_distance_m / (2.0 * (1.0 + template.aspect_ratio))
    base_height_m = template.aspect_ratio * base_width_m
    width_m = base_width_m * width_scale
    height_m = base_height_m * height_scale

    turn = 90.0 if template.direction == "cw" else -90.0
    bearing_ab = template.rotation_deg
    bearing_bc = bearing_ab + turn
    bearing_cd = bearing_bc + turn

    b = destination_point(start, bearing_ab, height_m)
    c = destination_point(b, bearing_bc, width_m)
    d = destination_point(c, bearing_cd, height_m)

    return (b, c, d)
