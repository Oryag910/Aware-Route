"""Versioned graph artifact packaging: manifest schema + validation.

The routing graph is expensive to build (an Overpass API fetch of all
Manhattan walk-network ways) so we build it once, offline, via
``scripts/build_graph.py`` and commit the resulting pickle + manifest as a
versioned artifact. This module defines that manifest and the checks that
must pass before a loaded graph is trusted at runtime.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

#: Manifest schema versions this code knows how to validate.
SUPPORTED_SCHEMA_VERSIONS = (1,)

#: Allowed relative drift between the manifest's recorded node/edge counts
#: and the counts observed in the loaded graph. Guards against a manifest
#: that was paired with the wrong pickle without being overly strict about
#: byte-for-byte reproducibility.
_COUNT_TOLERANCE = 0.05


class GraphArtifactError(Exception):
    """Raised when a graph artifact fails to validate."""


@dataclass(frozen=True)
class GraphManifest:
    artifact_version: str
    graph_schema_version: int
    created_at: str
    node_count: int
    edge_count: int
    sha256: str
    python_version: str
    osmnx_version: str
    networkx_version: str
    source_area: str
    has_elevation: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_manifest(manifest: GraphManifest, path: Path) -> None:
    path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n")


def read_manifest(path: Path) -> GraphManifest:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise GraphArtifactError(f"Manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GraphArtifactError(f"Manifest is not valid JSON: {path}") from exc

    try:
        return GraphManifest(**raw)
    except TypeError as exc:
        raise GraphArtifactError(
            f"Manifest at {path} is missing or has unexpected fields: {exc}"
        ) from exc


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as pickle_file:
        for chunk in iter(lambda: pickle_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _smoke_test_shortest_path(graph: Any) -> None:
    """A known shortest-path query must succeed on the loaded graph.

    This catches structural corruption (e.g. a pickle that unpickles fine
    but lost its spatial index/attrs) that a byte-count check would miss.
    """
    import networkx as nx

    if graph.number_of_nodes() == 0:
        raise GraphArtifactError("Loaded graph has zero nodes.")

    # Pick two nodes from the largest weakly-connected component so a path
    # is guaranteed to exist, rather than hardcoding OSM node IDs that may
    # shift between rebuilds.
    components = sorted(
        nx.weakly_connected_components(graph), key=len, reverse=True
    )
    if not components:
        raise GraphArtifactError("Loaded graph has no connected components.")

    largest = list(components[0])
    if len(largest) < 2:
        raise GraphArtifactError(
            "Largest connected component has fewer than 2 nodes; cannot "
            "smoke-test shortest path."
        )

    source, target = largest[0], largest[len(largest) // 2]

    try:
        path = nx.shortest_path(graph, source, target, weight="length")
    except (nx.NetworkXError, nx.NodeNotFound) as exc:
        raise GraphArtifactError(
            f"Shortest-path smoke test failed between {source} and "
            f"{target}: {exc}"
        ) from exc

    if not path or path[0] != source or path[-1] != target:
        raise GraphArtifactError(
            "Shortest-path smoke test returned an invalid path."
        )


def verify_artifact(pkl_path: Path, manifest_path: Path, graph: Any = None) -> GraphManifest:
    """Validate a graph artifact against its manifest.

    Checks (in order): manifest is readable, schema version is supported,
    the pickle's SHA-256 matches the manifest, node/edge counts are within
    tolerance, and a shortest-path smoke test succeeds.

    If ``graph`` is not provided, the pickle is loaded here to run the
    smoke test. Callers that already have the graph in memory (e.g. the
    loader, right after unpickling) should pass it in to avoid loading
    twice.

    Returns the validated manifest on success. Raises ``GraphArtifactError``
    with a clear message on any failure.
    """
    manifest = read_manifest(manifest_path)

    if manifest.graph_schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise GraphArtifactError(
            f"Unsupported graph schema version {manifest.graph_schema_version}; "
            f"this code supports {SUPPORTED_SCHEMA_VERSIONS}."
        )

    if not pkl_path.exists():
        raise GraphArtifactError(f"Graph artifact not found: {pkl_path}")

    actual_sha256 = sha256_of_file(pkl_path)
    if actual_sha256 != manifest.sha256:
        raise GraphArtifactError(
            f"SHA-256 mismatch for {pkl_path}: manifest says "
            f"{manifest.sha256}, actual is {actual_sha256}. The artifact "
            "may be corrupted or out of sync with its manifest."
        )

    if graph is None:
        import pickle

        with pkl_path.open("rb") as pickle_file:
            graph = pickle.load(pickle_file)

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()

    for label, actual, expected in (
        ("node", node_count, manifest.node_count),
        ("edge", edge_count, manifest.edge_count),
    ):
        if expected == 0:
            if actual != 0:
                raise GraphArtifactError(
                    f"Manifest expected 0 {label}s but graph has {actual}."
                )
            continue
        drift = abs(actual - expected) / expected
        if drift > _COUNT_TOLERANCE:
            raise GraphArtifactError(
                f"{label.capitalize()} count drift too large: manifest says "
                f"{expected}, graph has {actual} ({drift:.1%} off, "
                f"tolerance is {_COUNT_TOLERANCE:.0%})."
            )

    _smoke_test_shortest_path(graph)

    return manifest
