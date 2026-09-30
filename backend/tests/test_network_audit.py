"""Structural checking must fail on bad geometry, not silently connect it."""

from copy import deepcopy
import math

import pytest

from app.network_audit import audit_network, find_junction_candidates


def _graph() -> dict:
    return {"nodes": [{"id": name, "xMeters": x, "yMeters": y} for name, x, y in
                      [("a", 0, 0), ("b", 10, 0), ("c", 5, -5), ("d", 5, 5), ("alone", 100, 100)]],
            "edges": [{"id": "ab", "from": "a", "to": "b", "kind": "shared_way", "pathMeters": [[0, 0], [10, 0]]},
                      {"id": "cd", "from": "c", "to": "d", "kind": "shared_way", "pathMeters": [[5, -5], [5, 5]]}]}


def test_geometric_crossing_does_not_merge_components_and_audit_is_read_only() -> None:
    graph = _graph()
    unchanged = deepcopy(graph)
    report = audit_network(graph)
    assert report["componentCount"] == 3
    assert report["componentSizes"] == [2, 2, 1]
    assert report["terminalNodes"] == 4
    assert report["isolatedNodes"] == 1
    assert report["structuralErrors"] == 0
    assert graph == unchanged


@pytest.mark.parametrize("change", ["duplicate_node", "duplicate_edge", "missing_endpoint", "self_loop",
                                    "bad_kind", "shifted_path", "nan_node", "nan_path", "boolean_point",
                                    "bad_wait", "boolean_wait"])
def test_invalid_graph_is_rejected(change: str) -> None:
    graph = _graph()
    if change == "duplicate_node":
        graph["nodes"].append(deepcopy(graph["nodes"][0]))
    elif change == "duplicate_edge":
        graph["edges"].append(deepcopy(graph["edges"][0]))
    elif change == "missing_endpoint":
        graph["edges"][0]["to"] = "missing"
    elif change == "self_loop":
        graph["edges"][0]["to"] = "a"
    elif change == "bad_kind":
        graph["edges"][0]["kind"] = "motorway"
    elif change == "shifted_path":
        graph["edges"][0]["pathMeters"][0] = [1, 0]
    elif change == "nan_node":
        graph["nodes"][-1]["xMeters"] = math.nan
    elif change == "nan_path":
        graph["edges"][0]["pathMeters"][0] = [math.nan, 0]
    elif change == "boolean_point":
        graph["edges"][0]["pathMeters"][0] = [False, 0]
    else:
        graph["edges"][0]["kind"] = "crossing"
        graph["edges"][0]["waitSeconds"] = -1 if change == "bad_wait" else True
    with pytest.raises(ValueError):
        audit_network(graph)


def test_subcentimetre_endpoint_drift_is_rejected_like_cpp() -> None:
    graph = _graph()
    graph["edges"][0]["pathMeters"][0] = [0.001, 0]
    with pytest.raises(ValueError, match="geometry does not meet node"):
        audit_network(graph)


def test_nearby_unselected_junction_is_not_marked_as_reviewed() -> None:
    source = {"nodes": [{"id": "reviewed", "x": 0, "y": 0}, {"id": "nearby", "x": 10, "y": 0}],
              "edges": [{"id": f"{name}-{i}", "from": name, "to": f"outside-{name}-{i}",
                         "kind": "roadMajor"} for name in ("reviewed", "nearby") for i in range(3)]}
    reviewed = [{"id": "junction", "centerMeters": [0, 0], "rawNodeIds": ["reviewed"]}]
    # Supply actual endpoint nodes; candidates are only the two branch nodes.
    source["nodes"] += [{"id": edge["to"], "x": 100, "y": 100} for edge in source["edges"]]
    candidates = {r["rawNodeId"]: r for r in find_junction_candidates(source, reviewed)}
    assert candidates["reviewed"]["reviewedBy"] == ["junction"]
    assert candidates["nearby"]["reviewedBy"] == []
