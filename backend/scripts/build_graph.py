"""Build the versioned Manhattan walk-network graph artifact.

Fetches the OSM walk network for Manhattan via osmnx/Overpass, attempts to
attach free SRTM node elevations (best-effort -- skipped cleanly if
unavailable), then writes a versioned pickle + manifest to
``data/manhattan_walk_graph.v1.pkl`` / ``.v1.manifest.json``.

Also keeps writing the legacy unversioned ``.graphml`` / ``.gpickle`` files
for now, so other in-flight work depending on them is unaffected.

Usage:
    python scripts/build_graph.py

Reproducibility: re-running this script re-fetches the network from
Overpass (not guaranteed byte-identical, since OSM data changes over time)
and re-attaches elevation, producing a fresh artifact + manifest pair that
validates against each other via ``app.graph.packaging.verify_artifact``.
"""

import pickle
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import osmnx as ox

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.graph.packaging import GraphManifest, verify_artifact, write_manifest  # noqa: E402

PLACE_NAME = "Manhattan, New York, USA"
NETWORK_TYPE = "walk"
ARTIFACT_VERSION = "v1"
GRAPH_SCHEMA_VERSION = 1

DATA_DIR = Path(__file__).parents[1] / "data"
GRAPHML_PATH = DATA_DIR / "manhattan_walk_graph.graphml"
PICKLE_PATH = DATA_DIR / "manhattan_walk_graph.gpickle"

ARTIFACT_PICKLE_PATH = DATA_DIR / f"manhattan_walk_graph.{ARTIFACT_VERSION}.pkl"
ARTIFACT_MANIFEST_PATH = (
    DATA_DIR / f"manhattan_walk_graph.{ARTIFACT_VERSION}.manifest.json"
)


def build_graph() -> tuple[Any, float]:
    start = time.perf_counter()
    graph = ox.graph_from_place(PLACE_NAME, network_type=NETWORK_TYPE)
    elapsed = time.perf_counter() - start
    return graph, elapsed


def attach_elevations(graph: Any) -> bool:
    """Best-effort: attach a ``elevation`` attribute (meters) to every node.

    Uses the free ``srtm.py`` package, which downloads NASA SRTM tiles on
    demand and caches them locally (~/.cache/srtm). No API key required.

    Some SRTM pixels are "void" (no data, common in dense urban areas) and
    resolve to ``None``; those are filled via a small nearest-valid-neighbor
    search over adjacent SRTM grid cells, then a graph-wide median as a last
    resort so every node ends up with a numeric elevation.

    Returns True if elevations were attached, False if the step was skipped
    entirely (e.g. the ``srtm`` package isn't installed or the data source
    is unreachable). Never raises -- elevation is a nice-to-have, not a
    build blocker.
    """
    try:
        import srtm  # type: ignore[import-untyped]
    except ImportError:
        print("Elevation: 'srtm' package not installed -- skipping elevation.")
        return False

    try:
        srtm_data = srtm.get_data()
        # Prime the cache / confirm the source is reachable before we
        # commit to attaching elevation for the whole graph.
        probe_node = next(iter(graph.nodes(data=True)))[1]
        srtm_data.get_elevation(probe_node["y"], probe_node["x"])
    except Exception as exc:  # noqa: BLE001 -- any failure here means "skip"
        print(f"Elevation: SRTM source unreachable ({exc!r}) -- skipping elevation.")
        return False

    # SRTM grid spacing is ~0.000278 degrees (1 arc-second). Search a small
    # ring of offsets at that spacing to route around void pixels.
    step = 0.00028
    ring_offsets = [
        (dlat * step, dlon * step)
        for dlat in (-1, 0, 1)
        for dlon in (-1, 0, 1)
        if not (dlat == 0 and dlon == 0)
    ]

    void_count = 0
    unresolved_nodes: list[int] = []

    for node_id, node_data in graph.nodes(data=True):
        lat, lon = node_data["y"], node_data["x"]
        elevation = srtm_data.get_elevation(lat, lon)

        if elevation is None:
            void_count += 1
            for dlat, dlon in ring_offsets:
                elevation = srtm_data.get_elevation(lat + dlat, lon + dlon)
                if elevation is not None:
                    break

        if elevation is None:
            unresolved_nodes.append(node_id)
        else:
            node_data["elevation"] = float(elevation)

    if unresolved_nodes:
        known_elevations = [
            data["elevation"]
            for _, data in graph.nodes(data=True)
            if "elevation" in data
        ]
        if not known_elevations:
            print("Elevation: no node resolved an elevation -- skipping elevation.")
            return False
        fallback = sorted(known_elevations)[len(known_elevations) // 2]
        for node_id in unresolved_nodes:
            graph.nodes[node_id]["elevation"] = fallback

    print(
        f"Elevation: attached to all {graph.number_of_nodes()} nodes "
        f"({void_count} void-pixel lookups resolved via neighbor search, "
        f"{len(unresolved_nodes)} filled with graph median)."
    )
    return True


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching walk network for {PLACE_NAME!r}...")
    graph, build_seconds = build_graph()

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()

    print(f"Graph build time: {build_seconds:.2f}s")
    print(f"Nodes: {node_count}")
    print(f"Edges: {edge_count}")

    print("Attempting elevation attachment...")
    has_elevation = attach_elevations(graph)

    # Legacy unversioned files -- kept until every consumer has moved to
    # the versioned artifact, to avoid breaking concurrent in-flight work.
    ox.save_graphml(graph, filepath=GRAPHML_PATH)
    graphml_bytes = GRAPHML_PATH.stat().st_size

    with PICKLE_PATH.open("wb") as pickle_file:
        pickle.dump(graph, pickle_file, protocol=pickle.HIGHEST_PROTOCOL)
    pickle_bytes = PICKLE_PATH.stat().st_size

    print(f"GraphML size: {graphml_bytes / 1_048_576:.2f} MB ({GRAPHML_PATH})")
    print(f"Pickle size: {pickle_bytes / 1_048_576:.2f} MB ({PICKLE_PATH})")

    # Versioned artifact.
    with ARTIFACT_PICKLE_PATH.open("wb") as pickle_file:
        pickle.dump(graph, pickle_file, protocol=pickle.HIGHEST_PROTOCOL)

    import hashlib

    digest = hashlib.sha256()
    with ARTIFACT_PICKLE_PATH.open("rb") as pickle_file:
        for chunk in iter(lambda: pickle_file.read(1024 * 1024), b""):
            digest.update(chunk)

    manifest = GraphManifest(
        artifact_version=ARTIFACT_VERSION,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        node_count=node_count,
        edge_count=edge_count,
        sha256=digest.hexdigest(),
        python_version=platform.python_version(),
        osmnx_version=ox.__version__,
        networkx_version=nx.__version__,
        source_area=PLACE_NAME,
        has_elevation=has_elevation,
    )
    write_manifest(manifest, ARTIFACT_MANIFEST_PATH)

    artifact_bytes = ARTIFACT_PICKLE_PATH.stat().st_size
    print(
        f"Versioned artifact: {artifact_bytes / 1_048_576:.2f} MB "
        f"({ARTIFACT_PICKLE_PATH})"
    )
    print(f"Manifest written: {ARTIFACT_MANIFEST_PATH}")

    print("Validating artifact against manifest...")
    verify_artifact(ARTIFACT_PICKLE_PATH, ARTIFACT_MANIFEST_PATH, graph=graph)
    print("Artifact validated OK.")


if __name__ == "__main__":
    main()
