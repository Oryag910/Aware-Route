"""Route-quality metrics computed directly from the local walk graph.

These are pure functions over a `networkx.MultiDiGraph` and a
`node_path` (the sequence of node ids a generated route walks). They
feed the Phase-3 composite scorer as graph-native replacements for
metrics the old ORS-based pipeline derived from `extra_info` (waytype/
surface segments) -- the local engine has no such side-channel, so
these are computed from edge attributes (`highway`, `length`) and, for
elevation, from node attributes instead.

Each function is interpretable on its own and safe to unit test in
isolation from the scorer that will eventually combine them.
"""

from typing import Any


# `highway` classes considered pedestrian-friendly for `pedestrian_share`.
# Anything else (residential, primary, secondary, tertiary, unclassified,
# service, ...) counts as a vehicular street share.
PEDESTRIAN_HIGHWAY_CLASSES = frozenset(
    {
        "footway",
        "path",
        "pedestrian",
        "living_street",
        "track",
        "steps",
        "bridleway",
        "cycleway",
    }
)


def _min_length_edge(graph: Any, u: int, v: int) -> dict[str, Any]:
    """The parallel edge (by MultiDiGraph key) between u and v with the
    smallest `length`. Duplicated from `app.graph.model` since that
    helper is private (`_min_length_edge`) and this module must not
    import non-public names."""
    parallel_edges = graph[u][v]
    return min(parallel_edges.values(), key=lambda data: data["length"])


def _edge_highway(edge_data: dict[str, Any]) -> str:
    """Normalize the `highway` tag to a single string key.

    OSM sometimes stores a list of highway values on an edge (rare, but
    happens after graph simplification merges parallel ways of
    different classes). Take the first value in that case; fall back to
    "unknown" when the tag is missing entirely.
    """
    highway = edge_data.get("highway", "unknown")
    if isinstance(highway, list):
        return str(highway[0]) if highway else "unknown"
    return str(highway)


def edge_reuse_ratio(node_path: list[int]) -> float:
    """Fraction of directed edge-steps that revisit an already-used
    undirected edge.

    Walks `node_path` as a sequence of (u, v) hops, normalizes each hop
    to an undirected `frozenset({u, v})` key, and counts how many hops
    land on a key already seen. 0.0 means every hop crosses new ground;
    ~1.0 means the route is a pure out-and-back (every hop after the
    turnaround retraces a prior one). This is a signal, not a
    penalty -- an out-and-back is a valid requested shape, so the
    scorer decides how to weight it per-shape.

    Computed purely from node ids (no graph lookups needed), so it is
    cheap enough to run on every candidate.
    """
    hops = list(zip(node_path, node_path[1:]))
    if not hops:
        return 0.0

    seen: set[frozenset[int]] = set()
    reused = 0

    for u, v in hops:
        key = frozenset((u, v))
        if key in seen:
            reused += 1
        else:
            seen.add(key)

    return reused / len(hops)


def waytype_breakdown(graph: Any, node_path: list[int]) -> dict[str, float]:
    """Length-share by `highway` class along the path.

    Each hop's length is attributed to the min-length parallel edge's
    `highway` class (mirrors how `app.graph.model.path_distance_m`
    picks an edge per hop). Returns a dict of class -> fraction of
    total path length; values sum to ~1.0. Returns an empty dict for a
    path with fewer than two nodes (no edges to attribute).
    """
    totals: dict[str, float] = {}
    total_length = 0.0

    for u, v in zip(node_path, node_path[1:]):
        edge_data = _min_length_edge(graph, u, v)
        length = float(edge_data["length"])
        highway = _edge_highway(edge_data)

        totals[highway] = totals.get(highway, 0.0) + length
        total_length += length

    if total_length == 0.0:
        return {}

    return {highway: length / total_length for highway, length in totals.items()}


def pedestrian_share(graph: Any, node_path: list[int]) -> float:
    """0-1 length-share on pedestrian-friendly ways vs vehicular streets.

    Pedestrian-friendly classes are `PEDESTRIAN_HIGHWAY_CLASSES`
    (footway/path/pedestrian/living_street/track/steps/bridleway/
    cycleway); everything else (residential, primary, service, ...)
    counts as vehicular. This is the graph-native replacement for the
    ORS-extras-based surface metric, which the local engine has no data
    for. Returns 0.0 for a path with fewer than two nodes.
    """
    breakdown = waytype_breakdown(graph, node_path)
    return sum(
        share
        for highway, share in breakdown.items()
        if highway in PEDESTRIAN_HIGHWAY_CLASSES
    )


def elevation_gain_from_nodes(graph: Any, node_path: list[int]) -> float | None:
    """Sum of positive elevation deltas between consecutive nodes.

    Reads node `elevation` attributes (meters) along `node_path` and
    accumulates every uphill step. Returns `None` if any visited node
    lacks an `elevation` attribute -- elevation may not be baked into
    the graph yet (see plan doc), so callers must treat this as
    "unknown" rather than silently scoring 0.0 gain. Returns 0.0 for a
    path with fewer than two nodes (no deltas to sum).
    """
    if len(node_path) < 2:
        return 0.0

    elevations: list[float] = []
    for node in node_path:
        node_data = graph.nodes[node]
        if "elevation" not in node_data:
            return None
        elevations.append(float(node_data["elevation"]))

    gain = 0.0
    for lower, higher in zip(elevations, elevations[1:]):
        delta = higher - lower
        if delta > 0.0:
            gain += delta

    return gain


def corrective_loop_penalty(node_path: list[int], max_stub_depth: int = 2) -> float:
    """0-1 signal for tiny out-and-back spurs / degenerate correction
    loops appended to a route for length tuning.

    Heuristic: for every position in `node_path`, look for a *short*
    immediate there-and-back -- a window `node[i], ..., node[j]` with
    `node[i] == node[j]`, every node strictly between them distinct
    (so the window isn't part of a longer repeat), and a hop-span
    `j - i` of at most `2 * max_stub_depth`. Such a window is a stub:
    the walker goes `depth` hops away from a node and immediately
    retraces straight back to it, rather than continuing to fresh
    ground or a deliberate long turnaround. Returns the fraction of
    hops covered by any such window (deduplicated).

    `max_stub_depth` bounds how far "away" counts as a *short*, and
    therefore awkward, dip (default 2, i.e. up to a 4-hop round trip
    like A -> B -> C -> B -> A). Note this intentionally also flags the
    node immediately either side of *any* out-and-back's turnaround
    (the single node one hop before it always forms a depth-1 window
    with the node one hop after) -- a real turnaround is, locally, the
    same physical direction-reversal as a corrective stub. This is a
    deliberate simplification: the metric does not try to tell apart
    "this is the route's one deliberate turnaround" from "this is a
    spliced-in correction," since that requires intent the node_path
    alone doesn't encode. In practice this keeps the penalty bounded
    and small for long, deliberate out-and-backs (the flagged window
    is a fixed few hops out of many) while a route with one-or-more
    *additional* short stubs scores noticeably higher, since those
    stubs contribute more flagged hops on top of the same baseline.
    Shape intent (loop vs out-and-back) is instead the job of
    `edge_reuse_ratio`, which the scorer weighs separately.

    Returns 0.0 for a path with fewer than 3 nodes (no there-and-back
    window is possible).
    """
    if len(node_path) < 3:
        return 0.0

    total_hops = len(node_path) - 1
    stub_hop_indices: set[int] = set()

    for i in range(len(node_path)):
        for depth in range(1, max_stub_depth + 1):
            j = i + 2 * depth
            if j >= len(node_path):
                break

            window = node_path[i : j + 1]
            if window[0] == window[-1] and len(set(window)) == depth + 1:
                stub_hop_indices.update(range(i, j))

    return len(stub_hop_indices) / total_hops
