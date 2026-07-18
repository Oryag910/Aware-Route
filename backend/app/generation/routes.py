"""Node-path-aware route wrapper + quality-metric computation.

`generate_candidates`/`generate_amenity_aware` in `app/generation/engine.py`
return bare `RouteCandidate`s (geometry + distance only) -- the local
scorer needs the underlying `node_path` too (to compute graph-native
quality metrics) and the per-route quality metrics themselves. This
module defines that richer wrapper (`GeneratedRoute`) and the pure
function that computes its `QualityMetrics` from a graph + node_path,
kept separate from `engine.py` so the metric computation is easy to
unit test on synthetic graphs.
"""

from dataclasses import dataclass
from typing import Any, Literal

from app.generation.quality import (
    corrective_loop_penalty,
    edge_reuse_ratio,
    elevation_gain_from_nodes,
    pedestrian_share,
    waytype_breakdown,
)
from app.generation.shape_metrics import isoperimetric_quotient
from app.routing.provider import RouteCandidate


Shape = Literal["round", "out_and_back", "mix"]


@dataclass(frozen=True)
class QualityMetrics:
    edge_reuse_ratio: float
    pedestrian_share: float
    elevation_gain_m: float | None
    corrective_loop_penalty: float
    isoperimetric_quotient: float
    waytype_breakdown: dict[str, float]


@dataclass(frozen=True)
class GeneratedRoute:
    candidate: RouteCandidate
    node_path: list[int]
    shape: Shape
    quality: QualityMetrics


def compute_quality(
    graph: Any,
    node_path: list[int],
    candidate: RouteCandidate,
    shape: Shape,
) -> QualityMetrics:
    """Compute all graph-native quality metrics for one route.

    `shape` is accepted for symmetry with `GeneratedRoute` and possible
    future shape-conditional metrics, but every metric here is
    currently shape-agnostic -- shape-aware weighting happens in the
    scorer, not here.
    """
    del shape  # not yet used; kept for interface symmetry / future use

    return QualityMetrics(
        edge_reuse_ratio=edge_reuse_ratio(node_path),
        pedestrian_share=pedestrian_share(graph, node_path),
        elevation_gain_m=elevation_gain_from_nodes(graph, node_path),
        corrective_loop_penalty=corrective_loop_penalty(node_path),
        isoperimetric_quotient=isoperimetric_quotient(candidate.geometry),
        waytype_breakdown=waytype_breakdown(graph, node_path),
    )
