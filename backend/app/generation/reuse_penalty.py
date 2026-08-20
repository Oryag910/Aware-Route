from typing import Any

import networkx as nx


# How much dearer a reused outbound edge looks to the return-leg
# Dijkstra. High enough to push the return onto genuinely different
# streets, low enough that no alternative still loses to backtracking.
REUSE_PENALTY = 4.0


def _reuse_penalty_weight(
    outbound_pairs: set[frozenset[int]], penalty: float
) -> Any:
    """Build a networkx callable edge weight that inflates the cost of
    any edge whose endpoints were used on the outbound leg."""

    def weight(u: int, v: int, edge_dict: dict[Any, dict[str, Any]]) -> float:
        base = float(min(data["length"] for data in edge_dict.values()))
        if frozenset((u, v)) in outbound_pairs:
            return base * penalty
        return base

    return weight


def edge_pairs(node_path: list[int]) -> set[frozenset[int]]:
    """Undirected edge set implied by consecutive hops in `node_path`."""
    return {frozenset((u, v)) for u, v in zip(node_path, node_path[1:])}


def reuse_penalized_path(
    graph: Any,
    source: int,
    target: int,
    used_pairs: set[frozenset[int]],
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
