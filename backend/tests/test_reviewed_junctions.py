"""Reviewed corner models: connectivity without hidden free street crossings."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import heapq
import json
import math
import os
from pathlib import Path

import pytest

from app import engine
from app.junction_annotations import expand_reviewed_junction
from app.network_audit import audit_network
from app.synthetic_converter import convert_preview_graph


ROOT = Path(__file__).resolve().parents[2]
REVIEW_IDS = ["review-guofan-guoxiu", "review-yinxing-guoquan", "review-yingao-guoquan",
              "review-south-central", "review-southeast-outer", "review-southeast-outer-2",
              "review-north-outer", "review-central-shared"]


def _fixture(*, shared_east: bool = False, t_junction: bool = False,
             rotate_degrees: float = 0) -> tuple[dict, dict]:
    xy = {"c": (0, 0), "n": (0, 80), "e": (80, 0), "s": (0, -80), "w": (-80, 0)}
    angle = math.radians(rotate_degrees)
    nodes = []
    for name, (x, y) in xy.items():
        x, y = x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)
        nodes.append({"id": name, "x": x, "y": -y})
    edges = [{"id": "n", "from": "c", "to": "n", "kind": "roadMajor", "sourceWayId": 1},
             {"id": "s", "from": "s", "to": "c", "kind": "roadMajor", "sourceWayId": 1},
             {"id": "e", "from": "c", "to": "e",
              "kind": "roadLocal" if shared_east else "roadMajor", "sourceWayId": 2}]
    if not t_junction:
        edges.append({"id": "w", "from": "w", "to": "c", "kind": "roadMajor", "sourceWayId": 2})
    graph = {"schemaVersion": 1, "kind": "synthetic-road-graph", "coordinateSystem": "preview-local-v1",
             "nodes": nodes, "edges": edges,
             "selectionBoundary": [[[-100, -100], [100, -100], [100, 100], [-100, 100], [-100, -100]]]}
    annotation = {"id": "fixture", "centerMeters": [0, 0], "rawNodeIds": ["c"],
                  "verificationStatus": "geometry_inferred_unverified", "source": "synthetic unit fixture",
                  "approachGroups": [{"id": name, "edgeIds": [name]}
                                     for name in ("n", "e", "s", "w") if not (t_junction and name == "w")]}
    return graph, annotation


def _distances(graph: dict, start: str, *, crossings: bool = True) -> dict[str, float]:
    adjacency = {n["id"]: [] for n in graph["nodes"]}
    for edge in graph["edges"]:
        if edge["kind"] == "crossing" and not crossings:
            continue
        length = sum(math.dist(a, b) for a, b in zip(edge["pathMeters"], edge["pathMeters"][1:]))
        cost = length / 1.3 + (edge.get("waitSeconds", 20) if edge["kind"] == "crossing" else 0)
        adjacency[edge["from"]].append((edge["to"], cost))
        adjacency[edge["to"]].append((edge["from"], cost))
    distances, queue = {start: 0.0}, [(0.0, start)]
    while queue:
        seconds, current = heapq.heappop(queue)
        if seconds != distances[current]:
            continue
        for target, cost in adjacency[current]:
            candidate = seconds + cost
            if candidate < distances.get(target, math.inf):
                distances[target] = candidate
                heapq.heappush(queue, (candidate, target))
    return distances


def _local_graph(graph: dict, record: dict) -> dict:
    ids = {p["edgeId"] for p in record["ports"]} | {c["edgeId"] for c in record["connectors"]}
    edges = [e for e in graph["edges"] if e["id"] in ids]
    used = {n for e in edges for n in (e["from"], e["to"])}
    return {"nodes": [n for n in graph["nodes"] if n["id"] in used], "edges": edges}


@pytest.fixture(scope="module")
def full_graph() -> dict:
    return json.loads((ROOT / "data/networks/synthetic-preview.json").read_text("utf-8"))


@pytest.fixture
def cpp_binary(monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = Path(os.getenv("CPP_ENGINE_PATH", str(ROOT / "cpp-engine/build/isochrone_engine.exe")))
    if not binary.is_file():
        pytest.skip("C++ engine binary is not available")
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=binary))
    return binary


@pytest.mark.parametrize("rotation", [0, 37, 145])
def test_four_corner_model_is_rotation_independent_and_cannot_cross_for_free(rotation: float) -> None:
    source, annotation = _fixture(rotate_degrees=rotation)
    graph = convert_preview_graph(source, [121.505, 31.333], junction_annotations=[annotation])
    record = graph["sourceGraph"]["manualJunctionAnnotations"][0]
    assert len(record["ports"]) == 8
    edges = {e["id"]: e for e in graph["edges"]}
    assert audit_network(graph)["componentCount"] == 1
    for link in record["connectors"]:
        edge = edges[link["edgeId"]]
        walk = math.dist(*edge["pathMeters"]) / 1.3
        if edge["kind"] == "turn":
            assert _distances(graph, edge["from"], crossings=False)[edge["to"]] == pytest.approx(walk)
            assert "waitSeconds" not in edge
        else:
            assert edge["to"] not in _distances(graph, edge["from"], crossings=False)
            assert _distances(graph, edge["from"])[edge["to"]] == pytest.approx(walk + 20)


def test_shared_t_branch_can_turn_to_its_side_but_cannot_bypass_main_road_crossing() -> None:
    source, annotation = _fixture(shared_east=True, t_junction=True)
    graph = convert_preview_graph(source, [121.505, 31.333], junction_annotations=[annotation])
    record = graph["sourceGraph"]["manualJunctionAnnotations"][0]
    ports = {p["id"]: p for p in record["ports"]}
    assert len(ports) == 5
    assert len([c for c in record["connectors"] if c["kind"] == "turn"]) == 3
    east = ports["e-0"]["nodeId"]
    no_crossing = _distances(graph, east, crossings=False)
    nodes = {n["id"]: n for n in graph["nodes"]}
    for port in record["ports"]:
        if port["id"].startswith(("n-", "s-")):
            same_side = nodes[port["nodeId"]]["xMeters"] > 0
            assert (port["nodeId"] in no_crossing) is same_side
    assert len([e for e in graph["edges"] if e["kind"] == "crossing"]) == 2
    assert "synthetic-turn:e:c" in record["retiredEdgeIds"]


@pytest.mark.parametrize("change", ["empty_group", "missing_arm", "repeated_edge", "opposite_group",
                                    "wrong_node", "nan_center", "bad_setback", "verified"])
def test_invalid_review_fails_closed(change: str) -> None:
    source, annotation = _fixture()
    base = convert_preview_graph(source, [121.505, 31.333])
    annotation = deepcopy(annotation)
    if change == "empty_group":
        annotation["approachGroups"][0]["edgeIds"] = []
    elif change == "missing_arm":
        annotation["approachGroups"].pop()
    elif change == "repeated_edge":
        annotation["approachGroups"][1]["edgeIds"] = ["n"]
    elif change == "opposite_group":
        annotation["approachGroups"][0]["edgeIds"] = ["n", "s"]
    elif change == "wrong_node":
        annotation["rawNodeIds"] = ["unrelated"]
    elif change == "nan_center":
        annotation["centerMeters"] = [0, math.nan]
    elif change == "bad_setback":
        annotation["setbackMeters"] = True
    else:
        annotation["verificationStatus"] = "verified"
    with pytest.raises(ValueError):
        expand_reviewed_junction(annotation, {n["id"]: n for n in base["nodes"]}, base["edges"])


@pytest.mark.parametrize("record_id", REVIEW_IDS)
def test_each_checked_in_junction_has_closed_ports_and_charged_crossings(full_graph: dict, record_id: str) -> None:
    record = next(r for r in full_graph["sourceGraph"]["manualJunctionAnnotations"] if r["id"] == record_id)
    local = _local_graph(full_graph, record)
    edges = {e["id"]: e for e in local["edges"]}
    all_nodes = {n["id"] for n in full_graph["nodes"]}
    assert len({p["nodeId"] for p in record["ports"]}) == len(record["ports"])
    assert not {p["endpointNodeId"] for p in record["ports"]} & all_nodes
    assert not set(record["retiredEdgeIds"]) & {e["id"] for e in full_graph["edges"]}
    for connector in record["connectors"]:
        edge = edges[connector["edgeId"]]
        no_crossing = _distances(local, edge["from"], crossings=False)
        if edge["kind"] == "crossing":
            assert edge["waitSeconds"] == 20
            assert edge["to"] not in no_crossing
        else:
            assert edge["to"] in no_crossing
            assert "waitSeconds" not in edge
        assert edge["verificationStatus"] == "geometry_inferred_unverified"


@pytest.mark.parametrize("record_id", ["review-yinxing-guoquan", "review-yingao-guoquan"])
def test_dual_carriageway_median_ports_do_not_get_free_corner_connections(full_graph: dict, record_id: str) -> None:
    record = next(r for r in full_graph["sourceGraph"]["manualJunctionAnnotations"] if r["id"] == record_id)
    medians = [c for c in record["connectors"] if c["id"].startswith("median-")]
    if record_id == "review-yinxing-guoquan":
        assert len(record["ports"]) == 14
        assert len(medians) == 2
        assert set(record["terminalMedianPortIds"]) == {"north-0", "north-2"}
        assert not {"south-0", "south-2"} & {p["id"] for p in record["ports"]}
        assert {c["id"] for c in record["connectors"] if c["kind"] == "turn"} >= {
            "corner-west-south", "corner-south-east"}
        assert any(c["id"] == "cross-south" and c["waitSeconds"] == 20
                   for c in record["connectors"])
    else:
        assert len(record["ports"]) == 16
        assert len(medians) == 4
    median_ports = {c[key] for c in medians for key in ("fromPort", "toPort")}
    assert len(median_ports) == len(medians) * 2
    for connector in record["connectors"]:
        if connector["kind"] == "turn":
            assert connector["fromPort"] not in median_ports
            assert connector["toPort"] not in median_ports


def _payload(graph: dict, record: dict, *, threshold: float = 900) -> tuple[dict, str]:
    nodes = {n["id"]: n for n in graph["nodes"]}
    first = record["ports"][0]
    origin = nodes[first["nodeId"]]
    facilities = [{"id": p["id"], "accessEdgeId": p["edgeId"],
                   "accessPointMeters": [nodes[p["nodeId"]]["xMeters"], nodes[p["nodeId"]]["yMeters"]]}
                  for p in record["ports"][1:]]
    return {"schemaVersion": 2, "originMeters": origin, "originEdgeId": first["edgeId"],
            "thresholdSeconds": threshold, "walkingSpeedMetersPerSecond": 1.3,
            "crossingWaitSeconds": 20, "nodes": graph["nodes"], "edges": graph["edges"],
            "facilities": facilities}, origin["id"]


@pytest.mark.parametrize("record_id", REVIEW_IDS)
def test_cpp_local_route_costs_match_independent_oracle_with_and_without_crossings(
    full_graph: dict, record_id: str, cpp_binary: Path,
) -> None:
    record = next(r for r in full_graph["sourceGraph"]["manualJunctionAnnotations"] if r["id"] == record_id)
    local = _local_graph(full_graph, record)
    ports = {p["id"]: p["nodeId"] for p in record["ports"]}
    for with_crossing in (True, False):
        variant = {**local, "edges": [e for e in local["edges"] if with_crossing or e["kind"] != "crossing"]}
        payload, start = _payload(variant, record)
        expected = _distances(variant, start)
        result = asyncio.run(engine.run_engine(payload))
        for facility in result["facilityTravelTimes"]:
            target = expected.get(ports[facility["id"]])
            if target is None:
                assert facility["travelTimeSeconds"] is None
            else:
                assert facility["travelTimeSeconds"] == pytest.approx(target)


@pytest.mark.parametrize("record_id", REVIEW_IDS)
def test_cpp_full_network_port_routes_match_full_network_oracle(full_graph: dict, record_id: str, cpp_binary: Path) -> None:
    record = next(r for r in full_graph["sourceGraph"]["manualJunctionAnnotations"] if r["id"] == record_id)
    payload, start = _payload(full_graph, record, threshold=90)
    expected = _distances(full_graph, start)
    ports = {p["id"]: p["nodeId"] for p in record["ports"]}
    result = asyncio.run(engine.run_engine(payload))
    for facility in result["facilityTravelTimes"]:
        target = expected.get(ports[facility["id"]])
        if target is None:
            assert facility["travelTimeSeconds"] is None
        else:
            assert facility["travelTimeSeconds"] == pytest.approx(target)
