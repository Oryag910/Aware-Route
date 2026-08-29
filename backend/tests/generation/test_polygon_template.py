from math import isclose

from app.generation.polygon_template import (
    TEMPLATES,
    LoopTemplate,
    axis_scaled_template_anchors,
    template_anchors,
)
from app.routing.geometry import bearing_deg, destination_point, haversine_m
from app.routing.provider import Coordinate


START = Coordinate(lat=40.750, lon=-73.980)
TARGET_DISTANCE_M = 4000.0


def _perimeter_and_closure(
    start: Coordinate, template: LoopTemplate, scale: float = 1.0
) -> tuple[float, float]:
    """Walks all 4 legs (the 3 returned anchors plus one more step back
    toward start) and returns (perimeter, distance from where that walk
    actually lands back to `start`)."""
    b, c, d = template_anchors(start, TARGET_DISTANCE_M, template, scale)

    ab = haversine_m(start, b)
    bc = haversine_m(b, c)
    cd = haversine_m(c, d)

    # Walk the 4th leg the same way template_anchors built the first 3,
    # rather than just measuring haversine(d, start) -- that isolates
    # "does the bearing math actually close the loop" from "is d merely
    # near start by coincidence".
    turn = 90.0 if template.direction == "cw" else -90.0
    bearing_da = template.rotation_deg + 3 * turn
    da_length = bc  # leg D->A has the same length as B->C (both "width")
    closed_at = destination_point(d, bearing_da, da_length)

    perimeter = ab + bc + cd + da_length
    closure_gap = haversine_m(closed_at, start)
    return perimeter, closure_gap


def test_square_aspect_ratio_produces_equal_legs() -> None:
    square = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw")
    b, c, d = template_anchors(START, TARGET_DISTANCE_M, square)

    ab = haversine_m(START, b)
    bc = haversine_m(b, c)

    assert isclose(ab, bc, rel_tol=1e-6)


def test_aspect_ratio_scales_height_relative_to_width() -> None:
    tall = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.4, direction="cw")
    wide = LoopTemplate(rotation_deg=0.0, aspect_ratio=0.7, direction="cw")

    tall_b, tall_c, _ = template_anchors(START, TARGET_DISTANCE_M, tall)
    wide_b, wide_c, _ = template_anchors(START, TARGET_DISTANCE_M, wide)

    tall_height = haversine_m(START, tall_b)
    tall_width = haversine_m(tall_b, tall_c)
    wide_height = haversine_m(START, wide_b)
    wide_width = haversine_m(wide_b, wide_c)

    assert tall_height > tall_width
    assert wide_height < wide_width
    # height/width ratio should match the template's aspect_ratio.
    assert isclose(tall_height / tall_width, 1.4, rel_tol=1e-6)
    assert isclose(wide_height / wide_width, 0.7, rel_tol=1e-6)


def test_rotation_changes_leg_bearing() -> None:
    base = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw")
    rotated = LoopTemplate(rotation_deg=90.0, aspect_ratio=1.0, direction="cw")

    base_b, _, _ = template_anchors(START, TARGET_DISTANCE_M, base)
    rotated_b, _, _ = template_anchors(START, TARGET_DISTANCE_M, rotated)

    base_bearing = bearing_deg(START, base_b)
    rotated_bearing = bearing_deg(START, rotated_b)

    assert isclose(base_bearing, 0.0, abs_tol=0.5)
    assert isclose(rotated_bearing, 90.0, abs_tol=0.5)


def test_clockwise_and_counterclockwise_turn_opposite_ways() -> None:
    cw = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw")
    ccw = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="ccw")

    cw_b, cw_c, _ = template_anchors(START, TARGET_DISTANCE_M, cw)
    ccw_b, ccw_c, _ = template_anchors(START, TARGET_DISTANCE_M, ccw)

    # Both share the same A->B leg (bearing_deg 0 either way)...
    assert isclose(bearing_deg(START, cw_b), bearing_deg(START, ccw_b), abs_tol=0.5)
    # ...but B->C turns the opposite direction (east vs west).
    cw_bc_bearing = bearing_deg(cw_b, cw_c)
    ccw_bc_bearing = bearing_deg(ccw_b, ccw_c)
    assert isclose(cw_bc_bearing, 90.0, abs_tol=0.5)
    assert isclose(ccw_bc_bearing, 270.0, abs_tol=0.5)


def test_scale_scales_leg_lengths_proportionally() -> None:
    template = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw")

    b1, c1, _ = template_anchors(START, TARGET_DISTANCE_M, template, scale=1.0)
    b2, c2, _ = template_anchors(START, TARGET_DISTANCE_M, template, scale=2.0)

    leg1 = haversine_m(START, b1)
    leg2 = haversine_m(START, b2)

    assert isclose(leg2 / leg1, 2.0, rel_tol=1e-6)


def test_perimeter_approximately_matches_target_distance() -> None:
    for template in TEMPLATES:
        perimeter, _closure_gap = _perimeter_and_closure(START, template)
        assert isclose(perimeter, TARGET_DISTANCE_M, rel_tol=1e-6)


def test_every_template_closes_back_near_start() -> None:
    # The synthetic rectangle should close back to (approximately) its
    # own start point -- confirms the bearing/turn math is self
    # consistent, not just individually-plausible legs.
    for template in TEMPLATES:
        _perimeter, closure_gap = _perimeter_and_closure(START, template)
        assert closure_gap < 1.0  # meters -- spherical math, not exactly 0


def test_templates_cover_multiple_rotations_aspect_ratios_and_directions() -> None:
    rotations = {t.rotation_deg for t in TEMPLATES}
    aspect_ratios = {t.aspect_ratio for t in TEMPLATES}
    directions = {t.direction for t in TEMPLATES}

    assert len(rotations) >= 4
    assert len(aspect_ratios) >= 3
    assert directions == {"cw", "ccw"}


def test_deterministic_output() -> None:
    template = LoopTemplate(rotation_deg=30.0, aspect_ratio=1.2, direction="ccw")

    first = template_anchors(START, TARGET_DISTANCE_M, template)
    second = template_anchors(START, TARGET_DISTANCE_M, template)

    assert first == second


# ---------------------------------------------------------------------------
# axis_scaled_template_anchors -- the per-axis refinement's geometry helper
# ---------------------------------------------------------------------------


def test_axis_scaled_matches_template_anchors_when_scales_are_equal() -> None:
    """`height_scale == width_scale == s` must be identical to
    `template_anchors(..., scale=s)` -- the uniform-scale case is a
    special case of the axis-scaled one, not a separate code path."""
    template = LoopTemplate(rotation_deg=45.0, aspect_ratio=1.2, direction="cw")

    uniform = template_anchors(START, TARGET_DISTANCE_M, template, scale=1.3)
    axis_scaled = axis_scaled_template_anchors(
        START, TARGET_DISTANCE_M, template, height_scale=1.3, width_scale=1.3
    )

    assert uniform == axis_scaled


def test_axis_scaled_independently_scales_height_and_width_legs() -> None:
    """Shrinking `height_scale` alone must shrink ONLY the A->B/C->D
    ("height") legs, leaving the B->C ("width") leg exactly as it would
    be under `width_scale` alone -- this independence is the entire
    point of the refinement (redistributing budget between axes
    without a single shared knob)."""
    template = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw")

    baseline_b, baseline_c, _ = template_anchors(START, TARGET_DISTANCE_M, template, scale=1.0)
    baseline_height = haversine_m(START, baseline_b)
    baseline_width = haversine_m(baseline_b, baseline_c)

    b, c, _ = axis_scaled_template_anchors(
        START, TARGET_DISTANCE_M, template, height_scale=0.6, width_scale=1.5
    )
    height = haversine_m(START, b)
    width = haversine_m(b, c)

    assert isclose(height, baseline_height * 0.6, rel_tol=1e-6)
    assert isclose(width, baseline_width * 1.5, rel_tol=1e-6)


def test_axis_scaled_deterministic_output() -> None:
    template = LoopTemplate(rotation_deg=30.0, aspect_ratio=1.2, direction="ccw")

    first = axis_scaled_template_anchors(START, TARGET_DISTANCE_M, template, 0.6, 1.5)
    second = axis_scaled_template_anchors(START, TARGET_DISTANCE_M, template, 0.6, 1.5)

    assert first == second
