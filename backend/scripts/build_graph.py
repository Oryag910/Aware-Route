import pickle
import time
from pathlib import Path
from typing import Any

import osmnx as ox

PLACE_NAME = "Manhattan, New York, USA"
NETWORK_TYPE = "walk"

DATA_DIR = Path(__file__).parents[1] / "data"
GRAPHML_PATH = DATA_DIR / "manhattan_walk_graph.graphml"
PICKLE_PATH = DATA_DIR / "manhattan_walk_graph.gpickle"


def build_graph() -> tuple[Any, float]:
    start = time.perf_counter()
    graph = ox.graph_from_place(PLACE_NAME, network_type=NETWORK_TYPE)
    elapsed = time.perf_counter() - start
    return graph, elapsed


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching walk network for {PLACE_NAME!r}...")
    graph, build_seconds = build_graph()

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()

    print(f"Graph build time: {build_seconds:.2f}s")
    print(f"Nodes: {node_count}")
    print(f"Edges: {edge_count}")

    ox.save_graphml(graph, filepath=GRAPHML_PATH)
    graphml_bytes = GRAPHML_PATH.stat().st_size

    with PICKLE_PATH.open("wb") as pickle_file:
        pickle.dump(graph, pickle_file, protocol=pickle.HIGHEST_PROTOCOL)
    pickle_bytes = PICKLE_PATH.stat().st_size

    print(f"GraphML size: {graphml_bytes / 1_048_576:.2f} MB ({GRAPHML_PATH})")
    print(f"Pickle size: {pickle_bytes / 1_048_576:.2f} MB ({PICKLE_PATH})")


if __name__ == "__main__":
    main()
