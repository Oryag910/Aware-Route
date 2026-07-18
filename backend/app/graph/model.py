from typing import Any

from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


def node_coordinate(graph: Any, node_id: int) -> Coordinate:
    node = graph.nodes[node_id]
    return Coordinate(lat=node["y"], lon=node["x"])


def _min_length_edge(graph: Any, u: int, v: int) -> dict[str, Any]:
    """The parallel edge (by MultiDiGraph key) between u and v with the
    smallest `length`."""
    parallel_edges = graph[u][v]
    return min(parallel_edges.values(), key=lambda data: data["length"])


def path_distance_m(graph: Any, node_path: list[int]) -> float:
    total_m = 0.0

    for u, v in zip(node_path, node_path[1:]):
        total_m += _min_length_edge(graph, u, v)["length"]

    return total_m


def _edge_segment_coords(
    graph: Any, u: int, v: int, edge_data: dict[str, Any]
) -> list[tuple[float, float]]:
    """(lon, lat) points for one edge, oriented u -> v."""
    u_coord = node_coordinate(graph, u)
    v_coord = node_coordinate(graph, v)

    geometry = edge_data.get("geometry")

    if geometry is None:
        return [(u_coord.lon, u_coord.lat), (v_coord.lon, v_coord.lat)]

    coords = list(geometry.coords)
    first_lon, first_lat = coords[0]

    # A stored LineString may run either direction -- check which end
    # matches u's coordinate and reverse if needed.
    matches_start = (first_lon - u_coord.lon) ** 2 + (
        first_lat - u_coord.lat
    ) ** 2
    last_lon, last_lat = coords[-1]
    matches_end = (last_lon - u_coord.lon) ** 2 + (
        last_lat - u_coord.lat
    ) ** 2

    if matches_end < matches_start:
        coords = list(reversed(coords))

    return coords


def path_to_geometry(graph: Any, node_path: list[int]) -> tuple[RoutePoint, ...]:
    if not node_path:
        return ()

    if len(node_path) == 1:
        coord = node_coordinate(graph, node_path[0])
        return (RoutePoint(lat=coord.lat, lon=coord.lon, elevation_m=0.0),)

    points: list[RoutePoint] = []

    for u, v in zip(node_path, node_path[1:]):
        edge_data = _min_length_edge(graph, u, v)
        segment_coords = _edge_segment_coords(graph, u, v, edge_data)

        # Drop the duplicated shared vertex between consecutive edges.
        start_index = 1 if points else 0

        for lon, lat in segment_coords[start_index:]:
            points.append(RoutePoint(lat=lat, lon=lon, elevation_m=0.0))

    return tuple(points)


def path_to_candidate(graph: Any, node_path: list[int]) -> RouteCandidate:
    return RouteCandidate(
        geometry=path_to_geometry(graph, node_path),
        distance_m=path_distance_m(graph, node_path),
        elevation_gain_m=0.0,
        extras=None,
    )
