"""Street corners are free to turn, not free to cross a street."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import heapq
import json
import math
from pathlib import Path

import pytest

from app import engine
from app.junction_annotations import apply_junction_annotations
from app.synthetic_converter import convert_preview_graph


ROOT = Path(__file__).resolve().parents[2]


def _graph_and_record() -> tuple[dict, dict]:
    graph = json.loads((ROOT / "data/networks/synthetic-preview.json").read_text("utf-8"))
    return graph, graph["sourceGraph"]["manualJunctionAnnotations"][0]


def _local_graph() -> tuple[dict, dict]:
    graph, record = _graph_and_record()
    edge_ids = {p["edgeId"] for p in record["ports"]}
    edge_ids.update(c["edgeId"] for c in record["connectors"])
    edges = [e for e in graph["edges"] if e["id"] in edge_ids]
    node_ids = {n for e in edges for n in (e["from"], e["to"])}
    return {"nodes": [n for n in graph["nodes"] if n["id"] in node_ids],
            "edges": edges}, record


def _shortest(graph: dict, start: str, finish: str, *, crossings: bool = True) -> float | None:
    adjacency = {n["id"]: [] for n in graph["nodes"]}
    for edge in graph["edges"]:
        if not crossings and edge["kind"] == "crossing":
            continue
        cost = math.dist(*edge["pathMeters"]) / 1.3 + edge.get("waitSeconds", 0)
        adjacency[edge["from"]].append((edge["to"], cost))
        adjacency[edge["to"]].append((edge["from"], cost))
    distances = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        seconds, current = heapq.heappop(queue)
        if seconds != distances[current]:
            continue
        if current == finish:
            return seconds
        for target, cost in adjacency[current]:
            next_seconds = seconds + cost
            if next_seconds < distances.get(target, math.inf):
                distances[target] = next_seconds
                heapq.heappush(queue, (next_seconds, target))
    return None


def test_approaches_have_distinct_ports_and_old_free_center_is_removed() -> None:
    graph, record = _graph_and_record()
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = {e["id"]: e for e in graph["edges"]}
    assert len(record["ports"]) == 12
    assert len({p["nodeId"] for p in record["ports"]}) == 12
    assert len([c for c in record["connectors"] if c["kind"] == "turn"]) == 4
    assert len([c for c in record["connectors"] if c["kind"] == "crossing"]) == 6
    assert all(edge_id not in edges for edge_id in record["retiredEdgeIds"])
    assert not set(record["retiredEdgeIds"]) & set(graph["sourceGraph"]["syntheticLinkIds"])
    for port in record["ports"]:
        assert port["endpointNodeId"] not in nodes
        edge = edges[port["edgeId"]]
        assert port["nodeId"] in (edge["from"], edge["to"])
        assert port["endpointNodeId"] not in (edge["from"], edge["to"])
        assert all([nodes[id]["xMeters"], nodes[id]["yMeters"]] == point
                   for id, point in zip((edge["from"], edge["to"]), edge["pathMeters"]))


def test_each_corner_turns_without_wait_and_each_crossing_adds_twenty_seconds() -> None:
    graph, record = _local_graph()
    edges = {e["id"]: e for e in graph["edges"]}
    for connector in record["connectors"]:
        edge = edges[connector["edgeId"]]
        walk = math.dist(*edge["pathMeters"]) / 1.3
        if edge["kind"] == "turn":
            assert "waitSeconds" not in edge
            assert _shortest(graph, edge["from"], edge["to"], crossings=False) == pytest.approx(walk)
        else:
            assert edge["waitSeconds"] == 20
            assert _shortest(graph, edge["from"], edge["to"]) == pytest.approx(walk + 20)
            assert _shortest(graph, edge["from"], edge["to"], crossings=False) is None
        assert edge["verificationStatus"] == "user_marked_unverified"
        assert _shortest(graph, edge["to"], edge["from"]) == pytest.approx(walk + edge.get("waitSeconds", 0))


def test_all_four_outer_street_corners_are_connected_but_no_crossing_means_no_transfer() -> None:
    graph, record = _local_graph()
    ports = {p["id"]: p["nodeId"] for p in record["ports"]}
    corners = [ports[id] for id in ("ne-west", "sw-west", "sw-east", "ne-east")]
    for first in corners:
        for second in corners:
            assert _shortest(graph, first, second) is not None
            if first != second:
                assert _shortest(graph, first, second, crossings=False) is None


@pytest.mark.parametrize("change", ["missing_edge", "too_long", "verified", "turn_wait"])
def test_bad_junction_annotation_fails_without_a_silent_repair(change: str) -> None:
    graph = json.loads((ROOT / "frontend/src/data/demoRoadGraph.local.json").read_text("utf-8"))
    original = convert_preview_graph(graph, [121.505, 31.333])
    annotation = deepcopy(json.loads((ROOT / "data/networks/synthetic-preview.annotations.json")
                                    .read_text("utf-8"))["junctions"][0])
    if change == "missing_edge":
        annotation["ports"][0]["edgeId"] = "missing"
    elif change == "too_long":
        annotation["ports"][0]["setbackMeters"] = 1000
    elif change == "verified":
        annotation["verificationStatus"] = "verified"
    else:
        annotation["connectors"][0]["waitSeconds"] = 20
    with pytest.raises(ValueError):
        apply_junction_annotations({n["id"]: n for n in original["nodes"]},
                                   original["edges"], [annotation])


def test_full_cpp_turn_crossing_and_blocked_crossing(monkeypatch: pytest.MonkeyPatch) -> None:
    binary = ROOT / "cpp-engine/build/isochrone_engine.exe"
    if not binary.is_file():
        pytest.skip("C++ engine binary is not available")
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=binary))
    graph, record = _local_graph()
    nodes = {n["id"]: n for n in graph["nodes"]}
    ports = {p["id"]: p for p in record["ports"]}
    origin = nodes[ports["ne-west"]["nodeId"]]
    facilities = [{"id": id, "accessEdgeId": ports[id]["edgeId"],
                   "accessPointMeters": [nodes[ports[id]["nodeId"]]["xMeters"],
                                         nodes[ports[id]["nodeId"]]["yMeters"]]}
                  for id in ("nw-north", "ne-east", "nw-south", "sw-east")]
    payload = {"schemaVersion": 2, "originMeters": origin,
               "originEdgeId": ports["ne-west"]["edgeId"], "thresholdSeconds": 900,
               "walkingSpeedMetersPerSecond": 1.3, "crossingWaitSeconds": 20,
               "nodes": graph["nodes"], "edges": graph["edges"], "facilities": facilities}
    result = asyncio.run(engine.run_engine(payload))
    for facility in result["facilityTravelTimes"]:
        expected = _shortest(graph, origin["id"], ports[facility["id"]]["nodeId"])
        assert facility["travelTimeSeconds"] == pytest.approx(expected)
    payload["edges"] = [e for e in payload["edges"] if e["kind"] != "crossing"]
    blocked = asyncio.run(engine.run_engine(payload))
    times = {f["id"]: f["travelTimeSeconds"] for f in blocked["facilityTravelTimes"]}
    assert times["nw-north"] is not None
    assert all(times[id] is None for id in ("ne-east", "nw-south", "sw-east"))


def test_current_full_network_cpp_crossroad_not_only_an_isolated_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = ROOT / "cpp-engine/build/isochrone_engine.exe"
    if not binary.is_file():
        pytest.skip("C++ engine binary is not available")
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=binary))
    graph, record = _graph_and_record()
    local, _ = _local_graph()
    nodes = {n["id"]: n for n in graph["nodes"]}
    ports = {p["id"]: p for p in record["ports"]}
    origin = nodes[ports["ne-west"]["nodeId"]]
    targets = ["nw-north", "ne-east", "nw-south"]
    payload = {"schemaVersion": 2, "originMeters": origin,
               "originEdgeId": ports["ne-west"]["edgeId"], "thresholdSeconds": 90,
               "walkingSpeedMetersPerSecond": 1.3, "crossingWaitSeconds": 20,
               "nodes": graph["nodes"], "edges": graph["edges"],
               "facilities": [{"id": id, "accessEdgeId": ports[id]["edgeId"],
                               "accessPointMeters": [nodes[ports[id]["nodeId"]]["xMeters"],
                                                     nodes[ports[id]["nodeId"]]["yMeters"]]}
                              for id in targets]}
    result = asyncio.run(engine.run_engine(payload))
    for facility in result["facilityTravelTimes"]:
        expected = _shortest(local, origin["id"], ports[facility["id"]]["nodeId"])
        assert facility["travelTimeSeconds"] == pytest.approx(expected)
