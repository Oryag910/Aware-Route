import json
import pickle
from pathlib import Path

import networkx as nx
import pytest

from app.graph.packaging import (
    GraphArtifactError,
    GraphManifest,
    read_manifest,
    sha256_of_file,
    verify_artifact,
    write_manifest,
)
from tests.graph.conftest import NODE_A, NODE_B


def _write_pickle(graph: nx.MultiDiGraph, path: Path) -> None:
    with path.open("wb") as pickle_file:
        pickle.dump(graph, pickle_file, protocol=pickle.HIGHEST_PROTOCOL)


def _manifest_for(graph: nx.MultiDiGraph, pkl_path: Path) -> GraphManifest:
    return GraphManifest(
        artifact_version="v1",
        graph_schema_version=1,
        created_at="2026-01-01T00:00:00+00:00",
        node_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
        sha256=sha256_of_file(pkl_path),
        python_version="3.14.3",
        osmnx_version="2.1.0",
        networkx_version=nx.__version__,
        source_area="Test Area",
        has_elevation=False,
    )


@pytest.fixture
def artifact(
    tmp_path: Path, small_graph: nx.MultiDiGraph
) -> tuple[Path, Path, GraphManifest]:
    pkl_path = tmp_path / "graph.pkl"
    manifest_path = tmp_path / "graph.manifest.json"

    _write_pickle(small_graph, pkl_path)
    manifest = _manifest_for(small_graph, pkl_path)
    write_manifest(manifest, manifest_path)

    return pkl_path, manifest_path, manifest


def test_verify_artifact_passes_for_valid_graph(
    artifact: tuple[Path, Path, GraphManifest], small_graph: nx.MultiDiGraph
) -> None:
    pkl_path, manifest_path, manifest = artifact

    result = verify_artifact(pkl_path, manifest_path, graph=small_graph)

    assert result == manifest


def test_verify_artifact_loads_graph_itself_when_not_provided(
    artifact: tuple[Path, Path, GraphManifest],
) -> None:
    pkl_path, manifest_path, manifest = artifact

    result = verify_artifact(pkl_path, manifest_path)

    assert result == manifest


def test_verify_artifact_rejects_sha256_mismatch(
    artifact: tuple[Path, Path, GraphManifest], small_graph: nx.MultiDiGraph
) -> None:
    pkl_path, manifest_path, _manifest = artifact

    # Corrupt the pickle bytes without touching the manifest.
    corrupted = bytearray(pkl_path.read_bytes())
    corrupted[10:14] = b"\x00\x00\x00\x00"
    pkl_path.write_bytes(bytes(corrupted))

    with pytest.raises(GraphArtifactError, match="SHA-256 mismatch"):
        verify_artifact(pkl_path, manifest_path, graph=small_graph)


def test_verify_artifact_rejects_unsupported_schema_version(
    artifact: tuple[Path, Path, GraphManifest], small_graph: nx.MultiDiGraph
) -> None:
    pkl_path, manifest_path, manifest = artifact

    bad_manifest_path = manifest_path.parent / "bad.json"
    write_manifest(
        GraphManifest(**{**manifest.to_dict(), "graph_schema_version": 999}),
        bad_manifest_path,
    )

    with pytest.raises(GraphArtifactError, match="Unsupported graph schema version"):
        verify_artifact(pkl_path, bad_manifest_path, graph=small_graph)


def test_verify_artifact_rejects_node_count_drift(
    artifact: tuple[Path, Path, GraphManifest], small_graph: nx.MultiDiGraph
) -> None:
    pkl_path, manifest_path, manifest = artifact

    bad_manifest_path = manifest_path.parent / "bad_count.json"
    write_manifest(
        GraphManifest(**{**manifest.to_dict(), "node_count": 1}),
        bad_manifest_path,
    )

    with pytest.raises(GraphArtifactError, match="count drift too large"):
        verify_artifact(pkl_path, bad_manifest_path, graph=small_graph)


def test_verify_artifact_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(GraphArtifactError, match="Manifest not found"):
        verify_artifact(tmp_path / "nope.pkl", tmp_path / "nope.json")


def test_verify_artifact_rejects_missing_pickle(
    artifact: tuple[Path, Path, GraphManifest],
) -> None:
    _pkl_path, manifest_path, _manifest = artifact

    with pytest.raises(GraphArtifactError, match="not found"):
        verify_artifact(manifest_path.parent / "missing.pkl", manifest_path)


def test_verify_artifact_smoke_tests_shortest_path(
    tmp_path: Path, small_graph: nx.MultiDiGraph
) -> None:
    """F is disconnected from the rest of small_graph; a graph containing
    only F should fail the shortest-path smoke test (fewer than 2 nodes in
    its only component)."""
    isolated = nx.MultiDiGraph(crs="epsg:4326")
    isolated.add_node(99, x=1.0, y=1.0)

    pkl_path = tmp_path / "isolated.pkl"
    manifest_path = tmp_path / "isolated.manifest.json"
    _write_pickle(isolated, pkl_path)
    write_manifest(_manifest_for(isolated, pkl_path), manifest_path)

    with pytest.raises(GraphArtifactError, match="fewer than 2 nodes"):
        verify_artifact(pkl_path, manifest_path, graph=isolated)


def test_read_manifest_round_trips(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.json"
    manifest = GraphManifest(
        artifact_version="v1",
        graph_schema_version=1,
        created_at="2026-01-01T00:00:00+00:00",
        node_count=2,
        edge_count=1,
        sha256="a" * 64,
        python_version="3.14.3",
        osmnx_version="2.1.0",
        networkx_version="3.6.1",
        source_area="Test Area",
        has_elevation=True,
    )

    write_manifest(manifest, manifest_path)
    reloaded = read_manifest(manifest_path)

    assert reloaded == manifest


def test_read_manifest_rejects_malformed_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bad.json"
    manifest_path.write_text("{not valid json")

    with pytest.raises(GraphArtifactError, match="not valid JSON"):
        read_manifest(manifest_path)


def test_read_manifest_rejects_missing_fields(tmp_path: Path) -> None:
    manifest_path = tmp_path / "incomplete.json"
    manifest_path.write_text(json.dumps({"artifact_version": "v1"}))

    with pytest.raises(GraphArtifactError, match="missing or has unexpected fields"):
        read_manifest(manifest_path)


def test_sha256_of_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "sample.bin"
    path.write_bytes(b"hello graph world" * 1000)

    assert sha256_of_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


# Reference NODE_A/NODE_B so the shared fixtures module's constants stay
# exercised by this file too (keeps lint happy about unused imports while
# documenting that packaging tests build on the same synthetic graph).
def test_small_graph_fixture_has_expected_nodes(small_graph: nx.MultiDiGraph) -> None:
    assert NODE_A in small_graph.nodes
    assert NODE_B in small_graph.nodes
