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


def reuse_penalized_return_path(
    graph: Any,
    turnaround: int,
    start_node: int,
    outbound: list[int],
    penalty: float = REUSE_PENALTY,
) -> list[int] | None:
    """Shortest path turnaround -> start that avoids reusing outbound
    edges where an alternative exists. None if unreachable."""
    outbound_pairs = {
        frozenset((u, v)) for u, v in zip(outbound, outbound[1:])
    }
    try:
        path: list[int] = nx.shortest_path(
            graph,
            turnaround,
            start_node,
            weight=_reuse_penalty_weight(outbound_pairs, penalty),
        )
    except nx.NetworkXNoPath:
        return None
    return path
