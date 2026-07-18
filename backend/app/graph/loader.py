import pickle
from pathlib import Path
from typing import Any


GRAPH_PATH = Path(__file__).parents[2] / "data" / "manhattan_walk_graph.gpickle"


def load_graph() -> Any:
    with GRAPH_PATH.open("rb") as graph_file:
        return pickle.load(graph_file)


_graph: Any = None


def get_graph() -> Any:
    global _graph

    if _graph is None:
        _graph = load_graph()

    return _graph
