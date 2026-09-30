"""Only the named local median spans are removed; no automatic road merging."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from app import engine
from app.crossroad_audit import audit_explicit_junction
from app.divided_road_sections import apply_divided_road_sections
from app.network_audit import audit_network
from app.synthetic_converter import convert_preview_graph


ROOT = Path(__file__).resolve().parents[2]


def fixture() -> dict:
    return {
        "schemaVersion": 1, "kind": "synthetic-road-graph", "coordinateSystem": "preview-local-v1",
        "selectionBoundary": [[[-40, -120], [40, -120], [40, 120], [-40, 120], [-40, -120]]],
        "nodes": [{"id": name, "x": x, "y": y} for name, x, y in
                  [("wn", -8, -100), ("ws", -8, 100), ("en", 8, -100), ("es", 8, 100)]],
        "edges": [{"id": "west", "from": "wn", "to": "ws", "kind": "roadMajor", "sourceWayId": 1},
                  {"id": "east", "from": "es", "to": "en", "kind": "roadMajor", "sourceWayId": 2}],
        "dividedRoadSections": [{"id": "pilot", "medianWalkable": False,
            "verificationStatus": "user_marked_unverified", "source": "synthetic unit fixture",
            "boundsMeters": {"minX": -20, "maxX": 20, "minY": -50, "maxY": 50},
            "carriageways": [{"edgeId": "west", "outerSide": "right"}, {"edgeId": "east", "outerSide": "right"}]}],
    }


def slices(graph: dict, y: float) -> list[float]:
    crossings = []
    for edge in graph["edges"]:
        if edge["kind"] != "sidewalk":
            continue
        for a, b in zip(edge["pathMeters"], edge["pathMeters"][1:]):
            if min(a[1], b[1]) < y < max(a[1], b[1]):
                crossings.append(a[0] + (y-a[1])*(b[0]-a[0])/(b[1]-a[1]))
    return sorted(crossings)


def test_two_opposite_carriageways_keep_only_two_outer_sidewalks_locally() -> None:
    source = fixture()
    original = deepcopy(source)
    before_source = {**source, "dividedRoadSections": []}
    before = convert_preview_graph(before_source, [121.505, 31.333])
    after = convert_preview_graph(source, [121.505, 31.333])
    assert source == original
    assert slices(before, 0) == [-11, -5, 5, 11]
    assert slices(after, 0) == [-11, 11]
    assert slices(after, 75) == slices(before, 75)
    assert slices(after, -75) == slices(before, -75)
    assert all(e["kind"] == "sidewalk" for e in after["edges"])
    assert audit_network(after)["isolatedNodes"] == 0
    report = after["sourceGraph"]["dividedRoadSections"][0]
    assert sum(e["removedLengthMeters"] for e in report["maskedSidewalks"]) == 200


@pytest.mark.parametrize("change", ["inner_side", "duplicate_way", "unknown_way", "local_way", "nan_box",
                                    "boolean_box", "empty_box", "outside", "verified", "median", "duplicate_id"])
def test_invalid_or_wrong_side_section_is_rejected(change: str) -> None:
    source = fixture()
    section = source["dividedRoadSections"][0]
    if change == "inner_side": section["carriageways"][0]["outerSide"] = "left"
    elif change == "duplicate_way": section["carriageways"][1]["edgeId"] = "west"
    elif change == "unknown_way": section["carriageways"][0]["edgeId"] = "unknown"
    elif change == "local_way": source["edges"][0]["kind"] = "roadLocal"
    elif change == "nan_box": section["boundsMeters"]["minY"] = float("nan")
    elif change == "boolean_box": section["boundsMeters"]["minY"] = False
    elif change == "empty_box": section["boundsMeters"]["minY"] = 60
    elif change == "outside": section["boundsMeters"].update(minY=200, maxY=300)
    elif change == "verified": section["verificationStatus"] = "verified"
    elif change == "median": section["medianWalkable"] = True
    else: source["dividedRoadSections"].append(deepcopy(section))
    with pytest.raises(ValueError):
        convert_preview_graph(source, [121.505, 31.333])


def test_mask_cannot_silently_delete_a_marked_crossing_or_branch() -> None:
    source = fixture()
    base = convert_preview_graph({**source, "dividedRoadSections": []}, [121.505, 31.333])
    nodes = {n["id"]: n for n in base["nodes"]}
    # Explicitly connect an endpoint that a larger mask would remove.
    base["edges"].append({"id": "marked", "kind": "crossing", "from": "major:1:wn:left",
                          "to": "major:1:wn:right", "pathMeters": [[-5, 100], [-11, 100]]})
    source["dividedRoadSections"][0]["boundsMeters"].update(minY=-110, maxY=110)
    with pytest.raises(ValueError, match="connected node"):
        apply_divided_road_sections(source, nodes, base["edges"], [])


def test_checked_in_pilot_keeps_all_explicit_junctions_and_crossings_valid() -> None:
    source = json.loads((ROOT / "frontend/src/data/demoRoadGraph.local.json").read_text("utf-8"))
    annotations = json.loads((ROOT / "data/networks/synthetic-preview.annotations.json").read_text("utf-8"))
    after = convert_preview_graph(source, [121.505, 31.333], annotations["crossings"], annotations["junctions"])
    before = convert_preview_graph({**source, "dividedRoadSections": []}, [121.505, 31.333],
                                  annotations["crossings"], annotations["junctions"])
    assert after == json.loads((ROOT / "data/networks/synthetic-preview.json").read_text("utf-8"))
    assert len(after["sourceGraph"]["dividedRoadSections"]) == 1
    # Every checked-in junction annotation is applied exactly once; do not pin
    # the historical batch size here.
    assert len(after["sourceGraph"]["manualJunctionAnnotations"]) == len(annotations["junctions"])
    assert audit_network(after)["isolatedNodes"] == 0
    assert all(audit_explicit_junction(after, record)["pass"] for record in after["sourceGraph"]["manualJunctionAnnotations"])
    # The two explicitly named links to fictional inner sidewalks are retired;
    # every other crossing, turn and shared way stays byte-for-byte unchanged.
    retired = {f"manual-junction:review-yinxing-guoquan:{suffix}" for suffix in
               ("median-south-north-0", "median-south-north-1")}
    before_links = {e["id"]: e for e in before["edges"] if e["kind"] in ("crossing", "turn", "shared_way")}
    after_links = {e["id"]: e for e in after["edges"] if e["kind"] in ("crossing", "turn", "shared_way")}
    assert set(before_links) - set(after_links) == retired
    assert after_links == {edge_id: edge for edge_id, edge in before_links.items() if edge_id not in retired}
    assert not retired & set(after["sourceGraph"]["syntheticLinkIds"])
    assert not retired & set(after["sourceGraph"]["syntheticCrossingIds"])
    box = source["dividedRoadSections"][0]["boundsMeters"]
    for y in (-300, -350, -400):
        def local_slice(graph): return [x for x in slices(graph, y) if box["minX"] < x < box["maxX"]]
        assert len(local_slice(before)) == 4
        assert len(local_slice(after)) == 2
    # The north end now reaches the reviewed junction instead of leaving
    # short, connected inner-sidewalk stubs at its south approach.
    assert len([x for x in slices(before, -270) if box["minX"] < x < box["maxX"]]) == 4
    assert len([x for x in slices(after, -270) if box["minX"] < x < box["maxX"]]) == 2
    assert slices(before, -430) == pytest.approx(slices(after, -430), abs=0.001)


@pytest.mark.parametrize("change", ["missing_port", "missing_crossing", "corner_turn",
                                    "outside_bounds", "missing_junction", "extra_connection"])
def test_junction_closure_rejects_unaccounted_or_wrong_topology(change: str) -> None:
    source = json.loads((ROOT / "frontend/src/data/demoRoadGraph.local.json").read_text("utf-8"))
    annotations = json.loads((ROOT / "data/networks/synthetic-preview.annotations.json").read_text("utf-8"))
    section = source["dividedRoadSections"][0]
    closure = section["junctionApproachClosures"][0]
    if change == "missing_port": closure["portIds"][0] = "south-1"
    elif change == "missing_crossing": closure["connectorIds"].pop()
    elif change == "corner_turn": closure["connectorIds"][0] = "corner-south-east"
    elif change == "outside_bounds": section["boundsMeters"]["minY"] = 280
    elif change == "missing_junction": annotations["junctions"] = []
    else:
        # A second edge connected at the inner port must be reviewed, not
        # silently deleted along with the explicitly listed median crossing.
        record = next(j for j in annotations["junctions"] if j["id"] == closure["junctionId"])
        record["medianConnections"] = [{"id": "unlisted", "fromPort": "south-0", "toPort": "north-2"}]
    with pytest.raises(ValueError):
        convert_preview_graph(source, [121.505, 31.333], annotations["crossings"], annotations["junctions"])


def test_cpp_cannot_walk_the_median_or_cross_without_an_explicit_crossing(monkeypatch) -> None:
    binary = Path(os.getenv("CPP_ENGINE_PATH", str(ROOT / "cpp-engine/build/isochrone_engine.exe")))
    if not binary.is_file(): pytest.skip("C++ binary unavailable")
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=binary))
    graph = convert_preview_graph(fixture(), [121.505, 31.333])
    payload = {"schemaVersion": 2, "originMeters": {"xMeters": -11, "yMeters": 0},
               "originEdgeId": "west:right", "thresholdSeconds": 900,
               "walkingSpeedMetersPerSecond": 1.3, "crossingWaitSeconds": 20,
               "nodes": graph["nodes"], "edges": graph["edges"],
               "facilities": [{"id": "opposite", "accessEdgeId": "east:right", "accessPointMeters": [11, 0]}]}
    result = asyncio.run(engine.run_engine(payload))
    assert result["reachableEdges"]
    assert all(e["edgeId"] == "west:right" for e in result["reachableEdges"])
    assert result["facilityTravelTimes"][0]["travelTimeSeconds"] is None
    # Legal connector spans the whole road and pays one 20-second wait.
    graph["edges"].append({"id": "explicit-crossing", "kind": "crossing", "from": "major:1:wn:right",
                           "to": "major:2:en:right", "pathMeters": [[-11, 100], [11, 100]], "waitSeconds": 20})
    result = asyncio.run(engine.run_engine(payload))
    assert result["facilityTravelTimes"][0]["travelTimeSeconds"] == pytest.approx(222/1.3 + 20)
