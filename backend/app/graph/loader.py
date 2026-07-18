import logging
import pickle
import time
from pathlib import Path
from typing import Any

from app.graph.packaging import (
    GraphArtifactError,
    read_manifest,
    sha256_of_file,
    verify_artifact,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parents[2] / "data"

#: Versioned, committed artifact -- validated on every load via
#: ``verify_artifact`` (SHA-256, schema version, node/edge count, and a
#: shortest-path smoke test).
GRAPH_PATH = _DATA_DIR / "manhattan_walk_graph.v1.pkl"
MANIFEST_PATH = _DATA_DIR / "manhattan_walk_graph.v1.manifest.json"


def _process_rss_mb() -> float | None:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None

    rss_bytes: int = psutil.Process().memory_info().rss
    return rss_bytes / 1_048_576


def load_graph() -> Any:
    start = time.perf_counter()

    # Verify the manifest + SHA-256 against the raw bytes before attempting
    # to unpickle, so a corrupted/truncated file fails with a clear
    # GraphArtifactError instead of a raw UnpicklingError.
    if not GRAPH_PATH.exists():
        raise GraphArtifactError(
            f"Graph artifact not found at {GRAPH_PATH}. Run "
            "scripts/build_graph.py to generate it."
        )

    manifest = read_manifest(MANIFEST_PATH)
    actual_sha256 = sha256_of_file(GRAPH_PATH)
    if actual_sha256 != manifest.sha256:
        raise GraphArtifactError(
            f"SHA-256 mismatch for {GRAPH_PATH}: manifest says "
            f"{manifest.sha256}, actual is {actual_sha256}. The artifact "
            "may be corrupted or out of sync with its manifest."
        )

    try:
        with GRAPH_PATH.open("rb") as graph_file:
            graph = pickle.load(graph_file)
    except Exception as exc:
        raise GraphArtifactError(
            f"Failed to unpickle graph artifact at {GRAPH_PATH} even "
            f"though its SHA-256 matched the manifest: {exc!r}"
        ) from exc

    verify_artifact(GRAPH_PATH, MANIFEST_PATH, graph=graph)

    elapsed = time.perf_counter() - start
    rss_mb = _process_rss_mb()
    rss_note = f", RSS={rss_mb:.1f}MB" if rss_mb is not None else ""
    logger.info(
        "Loaded graph artifact %s in %.2fs (%d nodes, %d edges%s)",
        GRAPH_PATH.name,
        elapsed,
        graph.number_of_nodes(),
        graph.number_of_edges(),
        rss_note,
    )

    return graph


_graph: Any = None


def get_graph() -> Any:
    global _graph

    if _graph is None:
        _graph = load_graph()

    return _graph
