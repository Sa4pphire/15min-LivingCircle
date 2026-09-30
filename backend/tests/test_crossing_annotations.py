"""Reproducible user-marked crossings, without free sidewalk transfers."""

import asyncio
from dataclasses import replace
import json
import math
from pathlib import Path

import pytest

from app import engine
from app.synthetic_converter import convert_preview_graph


ROOT = Path(__file__).resolve().parents[2]


def _source() -> dict:
    return {
        "schemaVersion": 1, "kind": "synthetic-road-graph",
        "coordinateSystem": "preview-local-v1",
        "selectionBoundary": [[[-30, -30], [30, -30], [30, 30],
                               [-30, 30], [-30, -30]]],
        "nodes": [{"id": name, "x": x, "y": y} for name, x, y in (
            ("north", 10, -10), ("south", 10, 10),
            ("park-n", 0, -5), ("park-s", 0, 5))],
        "edges": [
            {"id": "major", "from": "north", "to": "south",
             "kind": "roadMajor", "sourceWayId": 1},
            {"id": "park", "from": "park-n", "to": "park-s",
             "kind": "roadPath", "sourceWayId": 2},
        ],
    }


def _annotations() -> list[dict]:
    return [{"id": name, "fromNodeId": f"shared:park-{name}",
             "nearEdgeId": "major:right", "farEdgeId": "major:left",
             "waitSeconds": 20, "verificationStatus": "user_marked_unverified",
             "source": "synthetic unit-test image annotation"} for name in ("n", "s")]


def test_two_marked_locations_split_sidewalks_and_keep_explicit_crossings() -> None:
    original = convert_preview_graph(_source(), [121.505, 31.333])
    converted = convert_preview_graph(_source(), [121.505, 31.333], _annotations())
    assert len(converted["nodes"]) == len(original["nodes"]) + 4
    assert len(converted["edges"]) == len(original["edges"]) + 8
    assert len([e for e in converted["edges"] if e["kind"] == "sidewalk"]) == 6
    assert len(converted["sourceGraph"]["manualCrossingAnnotations"]) == 2
    by_id = {e["id"]: e for e in converted["edges"]}
    for name in ("n", "s"):
        turn = by_id[f"manual-turn:{name}"]
        crossing = by_id[f"manual-crossing:{name}"]
        assert turn["from"] == f"shared:park-{name}"
        assert turn["kind"] == "turn"
        assert "waitSeconds" not in turn
        assert crossing["kind"] == "crossing" and crossing["waitSeconds"] == 20
        assert turn["to"] == crossing["from"]
        assert crossing["pathMeters"][0][0] == 7
        assert crossing["pathMeters"][1][0] == 13
        assert crossing["verificationStatus"] == "user_marked_unverified"
    # Splitting preserves total sidewalk geometry instead of duplicating it.
    for side in ("left", "right"):
        length = sum(math.dist(e["pathMeters"][0], e["pathMeters"][-1])
                     for e in converted["edges"] if e.get("side") == side)
        assert length == pytest.approx(20)


@pytest.mark.parametrize("change", [
    {"nearEdgeId": "missing"}, {"farEdgeId": "park"},
    {"farEdgeId": "major:right"}, {"waitSeconds": -1},
    {"waitSeconds": True}, {"verificationStatus": "verified"},
])
def test_invalid_annotations_fail_instead_of_silently_linking(change: dict) -> None:
    annotation = {**_annotations()[0], **change}
    with pytest.raises(ValueError):
        convert_preview_graph(_source(), [121.505, 31.333], [annotation])


def test_checked_in_markers_attach_both_blue_points_to_main_road() -> None:
    graph = json.loads((ROOT / "data/networks/synthetic-preview.json").read_text("utf-8"))
    by_id = {edge["id"]: edge for edge in graph["edges"]}
    # Additional reviewed junctions must not invalidate the original markers.
    # Whole-network reproducibility is covered by test_synthetic_converter.
    assert {r["id"] for r in graph["sourceGraph"]["manualCrossingAnnotations"]} == {
        "park-loop-north", "park-loop-south"}
    for name, branch in (("north", "shared:p:54.5:-501.1"),
                         ("south", "shared:p:-10.6:-430.4")):
        turn = by_id[f"manual-turn:park-loop-{name}"]
        crossing = by_id[f"manual-crossing:park-loop-{name}"]
        assert turn["from"] == branch and turn["to"] == crossing["from"]
        assert crossing["waitSeconds"] == 20
        for anchor in (crossing["from"], crossing["to"]):
            incident = [e for e in graph["edges"] if anchor in (e["from"], e["to"])]
            assert len([e for e in incident if e["kind"] == "sidewalk"]) == 2


def test_cpp_charges_wait_and_cannot_cross_without_the_explicit_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = ROOT / "cpp-engine/build/isochrone_engine.exe"
    if not binary.is_file():
        pytest.skip("C++ engine binary is not available")
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=binary))
    graph = convert_preview_graph(_source(), [121.505, 31.333], [_annotations()[0]])
    far_edge = next(e for e in graph["edges"]
                    if e["kind"] == "sidewalk" and e.get("side") == "left"
                    and "manual:n:far" in (e["from"], e["to"]))
    payload = {"schemaVersion": 2, "originMeters": {"xMeters": 0, "yMeters": 5},
               "originEdgeId": "park", "thresholdSeconds": 900,
               "walkingSpeedMetersPerSecond": 1.3, "crossingWaitSeconds": 20,
               "nodes": graph["nodes"], "edges": graph["edges"],
               "facilities": [{"id": "opposite", "accessEdgeId": far_edge["id"],
                               "accessPointMeters": [13, 5]}]}
    result = asyncio.run(engine.run_engine(payload))
    assert result["facilityTravelTimes"][0]["travelTimeSeconds"] == pytest.approx(13 / 1.3 + 20)
    payload["edges"] = [e for e in payload["edges"] if e["kind"] != "crossing"]
    blocked = asyncio.run(engine.run_engine(payload))
    assert blocked["facilityTravelTimes"][0]["travelTimeSeconds"] is None


@pytest.mark.parametrize("name", ["north", "south"])
def test_full_graph_cpp_uses_the_marked_crossing_with_one_wait(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = ROOT / "cpp-engine/build/isochrone_engine.exe"
    if not binary.is_file():
        pytest.skip("C++ engine binary is not available")
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=binary))
    graph = json.loads((ROOT / "data/networks/synthetic-preview.json").read_text("utf-8"))
    record = next(r for r in graph["sourceGraph"]["manualCrossingAnnotations"]
                  if r["id"] == f"park-loop-{name}")
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = {e["id"]: e for e in graph["edges"]}
    branch = nodes[record["fromNodeId"]]
    far = nodes[record["farNodeId"]]
    origin_edge = next(e for e in graph["edges"] if e["kind"] == "shared_way"
                       and branch["id"] in (e["from"], e["to"]))
    far_edge = next(e for e in graph["edges"] if e["kind"] == "sidewalk"
                    and far["id"] in (e["from"], e["to"]))
    payload = {"schemaVersion": 2,
               "originMeters": {"xMeters": branch["xMeters"], "yMeters": branch["yMeters"]},
               "originEdgeId": origin_edge["id"], "thresholdSeconds": 60,
               "walkingSpeedMetersPerSecond": 1.3, "crossingWaitSeconds": 20,
               "nodes": graph["nodes"], "edges": graph["edges"],
               "facilities": [{"id": "across", "accessEdgeId": far_edge["id"],
                               "accessPointMeters": [far["xMeters"], far["yMeters"]]}]}
    expected = 20 + sum(math.dist(edges[record[key]]["pathMeters"][0],
                                 edges[record[key]]["pathMeters"][-1]) / 1.3
                        for key in ("turnEdgeId", "crossingEdgeId"))
    result = asyncio.run(engine.run_engine(payload))
    assert result["facilityTravelTimes"][0]["travelTimeSeconds"] == pytest.approx(expected)
    assert record["crossingEdgeId"] in {e["edgeId"] for e in result["reachableEdges"]}
