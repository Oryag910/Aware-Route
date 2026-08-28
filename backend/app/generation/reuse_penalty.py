from typing import Any

import networkx as nx


# How much dearer a reused outbound edge looks to the return-leg
# Dijkstra. High enough to push the return onto genuinely different
# streets, low enough that no alternative still loses to backtracking.
REUSE_PENALTY = 4.0


def _reuse_penalty_weight(
    outbound_pairs: set[tuple[int, int]], penalty: float
) -> Any:
    """Build a networkx callable edge weight that inflates the cost of
    any edge whose endpoints were used on the outbound leg.

    This is the single hottest call in every reuse-penalized Dijkstra
    (networkx invokes it once per edge relaxation) -- profiling a
    multi-leg polygon-loop build showed it alone accounting for roughly
    half of total wall-clock time, split between two allocations this
    version avoids: `min(... for ...)` builds a throwaway generator on
    every call even though the walk graph has exactly one parallel edge
    the overwhelming majority of the time, and `frozenset((u, v))`
    allocates a new hash-table-backed object per call just to test
    membership. `outbound_pairs` uses plain `(min(u, v), max(u, v))`
    tuples instead (see `edge_pairs`) -- equivalent as an undirected-edge
    key, but a 2-tuple is far cheaper to construct and hash than a
    frozenset.
    """

    def weight(u: int, v: int, edge_dict: dict[Any, dict[str, Any]]) -> float:
        if len(edge_dict) == 1:
            base = float(next(iter(edge_dict.values()))["length"])
        else:
            base = float(min(data["length"] for data in edge_dict.values()))
        key = (u, v) if u <= v else (v, u)
        return base * penalty if key in outbound_pairs else base

    return weight


def edge_pairs(node_path: list[int]) -> set[tuple[int, int]]:
    """Undirected edge set implied by consecutive hops in `node_path`,
    each pair canonicalized as `(min(u, v), max(u, v))` -- see
    `_reuse_penalty_weight` for why this replaced `frozenset`."""
    return {(u, v) if u <= v else (v, u) for u, v in zip(node_path, node_path[1:])}


def reuse_penalized_path(
    graph: Any,
    source: int,
    target: int,
    used_pairs: set[tuple[int, int]],
    penalty: float = REUSE_PENALTY,
) -> list[int] | None:
    """Shortest path source -> target that avoids reusing any edge in
    `used_pairs` where an alternative exists. None if unreachable.

    General form of `reuse_penalized_return_path` -- source/target and
    the already-used edge set are caller-supplied instead of being
    fixed to "turnaround -> start avoiding the outbound leg", so a
    multi-leg generator (e.g. a polygon loop) can penalize every prior
    leg's edges when routing each subsequent one.
    """
    try:
        path: list[int] = nx.shortest_path(
            graph,
            source,
            target,
            weight=_reuse_penalty_weight(used_pairs, penalty),
        )
    except nx.NetworkXNoPath:
        return None
    return path


def reuse_penalized_return_path(
    graph: Any,
    turnaround: int,
    start_node: int,
    outbound: list[int],
    penalty: float = REUSE_PENALTY,
) -> list[int] | None:
    """Shortest path turnaround -> start that avoids reusing outbound
    edges where an alternative exists. None if unreachable."""
    return reuse_penalized_path(
        graph, turnaround, start_node, edge_pairs(outbound), penalty
    )
