"""Identify ordinary *source* junctions without inventing geometric connections.

The result is an unverified annotation proposal, not a walking-graph mutation.
Only single-node junctions are supported; template application remains separate.
"""

from collections import defaultdict
import hashlib
import math
from typing import Any, Iterable


OBSERVATION_LENGTHS = (20.0, 30.0, 40.0)
MIN_TRACE_METERS = 3.0
STABILITY_DEGREES = 15.0
OPPOSITE_TOLERANCE = 30.0
PARALLEL_TOLERANCE = 25.0


def _delta(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _xy(node: dict) -> tuple[float, float]:
    return node["x"], -node["y"]  # preview-local-v1 -> east/north metres


def _bearing(a: tuple, b: tuple) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360


def _tags(edge: dict) -> dict:
    return edge.get("sourceTags", {})


def _semantic_key(edge: dict) -> tuple:
    tags = _tags(edge)
    return (edge["kind"], *(str(tags.get(k, default)) for k, default in (
        ("highway", ""), ("layer", "0"), ("bridge", "no"),
        ("tunnel", "no"), ("foot", ""), ("access", ""))))


def _source_evidence(node: dict, arms: list[dict], junctions: list[dict], ways: dict) -> tuple:
    """Require explicit node identity and incident-way references, never proximity."""
    records = [j for j in junctions if j.get("nodeId") == node["id"] or (
        node.get("sourceNodeId") is not None
        and str(j.get("sourceNodeId")) == str(node["sourceNodeId"]))]
    source_ids = {str(j["sourceNodeId"]) for j in records if j.get("sourceNodeId") is not None}
    if node.get("sourceNodeId") is not None:
        source_ids.add(str(node["sourceNodeId"]))
    if len(source_ids) != 1:
        return None, {}, "source_refs_missing"
    source_id = (node["sourceNodeId"] if node.get("sourceNodeId") is not None
                 else records[0]["sourceNodeId"])
    incident = {str(e.get("sourceWayId")) for e in arms}
    if "None" in incident:
        return source_id, {}, "source_refs_missing"
    node_tags = {**(records[0].get("nodeTags", {}) if records else {}), **node.get("sourceTags", {})}
    if records:
        if any({str(w) for w in j.get("wayIds", [])} != incident for j in records):
            return source_id, node_tags, "source_topology_incomplete"
        return source_id, node_tags, None
    referenced = {wid for wid, w in ways.items() if str(source_id) in {
        str(n) for n in w.get("nodeIds", [])}}
    if not incident or incident != referenced:
        return source_id, node_tags, "source_refs_missing"
    return source_id, node_tags, None


def _restriction(arms: list[dict], node_tags: dict) -> str | None:
    if node_tags.get("barrier") not in (None, "no"):
        return "source_node_access_review_required"
    levels = {(str(_tags(e).get("layer", "0")), str(_tags(e).get("bridge", "no")),
               str(_tags(e).get("tunnel", "no"))) for e in arms}
    if levels != {("0", "no", "no")}:
        return "layer_or_structure_unsupported"
    for edge in arms:
        tags = _tags(edge)
        foot = tags.get("foot")
        if (foot in ("no", "private", "use_sidepath") or
                (tags.get("access") in ("no", "private") and foot not in ("yes", "designated", "permissive"))):
            return "pedestrian_access_restricted"
        if (tags.get("oneway:foot") not in (None, "no", "0") or
                tags.get("foot:forward") == "no" or tags.get("foot:backward") == "no"):
            return "walking_direction_unsupported"
        if tags.get("junction") in ("roundabout", "circular") or str(tags.get("highway", "")).endswith("_link"):
            return "complex_road_structure"
        if (edge.get("sourceCrossing") or edge["kind"] == "roadPath" or
                tags.get("highway") in ("footway", "path", "steps", "pedestrian", "cycleway")):
            return "independent_walking_network"
    return None


def _trace_arm(root: str, first: dict, nodes: dict, adjacency: dict, semantic_nodes: set[str]) -> dict:
    points = [_xy(nodes[root])]
    lengths = [0.0]
    seen = {root}
    current, edge = root, first
    traced = []
    while True:
        other = edge["to"] if edge["from"] == current else edge["from"]
        if other in seen:
            return {"edgeId": first["id"], "reason": "loop_or_roundabout"}
        seen.add(other)
        point = _xy(nodes[other])
        segment = math.dist(points[-1], point)
        if segment < 1e-6:
            return {"edgeId": first["id"], "reason": "approach_too_short"}
        points.append(point)
        lengths.append(lengths[-1] + segment)
        traced.append(edge["id"])
        if (lengths[-1] >= OBSERVATION_LENGTHS[-1] or len(adjacency[other]) != 2
                or other in semantic_nodes
                or nodes[other].get("boundaryClipped")):
            break
        following = next(e for e in adjacency[other] if e["id"] != edge["id"])
        if _semantic_key(following) != _semantic_key(edge):
            break
        current, edge = other, following

    def at(distance: float) -> tuple:
        for i in range(1, len(points)):
            if lengths[i] >= distance:
                f = (distance - lengths[i - 1]) / (lengths[i] - lengths[i - 1])
                return tuple(points[i - 1][k] + f * (points[i][k] - points[i - 1][k]) for k in (0, 1))
        return points[-1]

    result = {"edgeId": first["id"], "sourceWayId": first.get("sourceWayId"), "traceEdgeIds": traced}
    for requested in OBSERVATION_LENGTHS:
        length = min(requested, lengths[-1])
        if length < MIN_TRACE_METERS:
            return {**result, "reason": "approach_too_short"}
        half, full = at(length / 2), at(length)
        if min(math.dist(points[0], half), math.dist(points[0], full)) < 1e-6:
            return {**result, "reason": "direction_unstable"}
        angle = _bearing(points[0], full)
        if _delta(_bearing(points[0], half), angle) <= STABILITY_DEGREES:
            return {**result, "angleDeg": angle, "traceLengthMeters": length, "reason": None}
        if length == lengths[-1]:
            break
    return {**result, "reason": "direction_unstable"}


def _classify(angles: list[float]) -> tuple[str | None, str | None]:
    if any(_delta(a, b) <= PARALLEL_TOLERANCE for i, a in enumerate(angles) for b in angles[i + 1:]):
        return None, "parallel_or_fork_ambiguity"
    opposite = {(i, j) for i, a in enumerate(angles) for j, b in enumerate(angles)
                if i < j and abs(_delta(a, b) - 180) <= OPPOSITE_TOLERANCE + 1e-8}
    if len(angles) == 3:
        if len(opposite) > 1:
            return None, "ambiguous_opposite_pairs"
        shape = "T" if opposite else "Y"
    else:
        pairings = (((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2)))
        valid = [p for p in pairings if all(pair in opposite for pair in p)]
        if len(valid) != 1:
            return None, "ambiguous_or_missing_opposite_pairs"
        gaps = [(angles[(i + 1) % 4] - a) % 360 for i, a in enumerate(angles)]
        if min(gaps) < 35 or max(gaps) > 145:
            return None, "irregular_four_way"
        shape = "four_way"
    gaps = [(angles[(i + 1) % len(angles)] - a) % 360 for i, a in enumerate(angles)]
    return (shape, "template_angle_not_supported") if min(gaps) < 25 or max(gaps) > 195 else (shape, None)


def detect_junctions(source: dict[str, Any], *, node_ids: Iterable[str] | None = None,
                     protected_node_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Return ordinary source-node candidates; no source or walking data is changed.

    ``ready`` means a structural proposal is available. The caller must still
    validate its walking-edge mapping, template ports and crossing choices.
    Explicit node/way references are required; inferredJunction links are ignored.
    """
    if source.get("coordinateSystem") != "preview-local-v1":
        raise ValueError("junction detection requires preview-local-v1 source coordinates")
    nodes = {n["id"]: n for n in source["nodes"]}
    adjacency: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for edge in sorted(source["edges"], key=lambda e: e["id"]):
        if edge["kind"] == "inferredJunction":
            continue
        key = (edge.get("sourceWayId"), tuple(sorted((edge["from"], edge["to"]))), _semantic_key(edge))
        if key in seen:
            continue
        seen.add(key)
        for endpoint in (edge["from"], edge["to"]):
            if endpoint not in nodes:
                raise ValueError("source edge refers to a missing node")
            adjacency[endpoint].append(edge)
    topology = source.get("sourceTopology", {})
    junctions = topology.get("junctions", [])
    ways = {str(w.get("id", w.get("sourceWayId"))): w for w in topology.get("ways", [])}
    tagged_refs = {str(j["sourceNodeId"]) for j in junctions if j.get("nodeTags") and j.get("sourceNodeId") is not None}
    semantic_nodes = {n["id"] for n in nodes.values() if n.get("sourceTags") or n.get("nodeTags")
                      or str(n.get("sourceNodeId")) in tagged_refs}
    semantic_nodes.update(j["nodeId"] for j in junctions if j.get("nodeId") in nodes and j.get("nodeTags"))
    selected = None if node_ids is None else set(node_ids)
    protected = set(protected_node_ids)
    divided = {s.get("sourceEdgeId") for s in source.get("majorSidewalkPolicy", {}).get("selections", [])
               if s.get("model") == "paired_carriageway" or len(s.get("sides", [])) == 1}
    result = []
    for nid in sorted(adjacency):
        arms = adjacency[nid]
        if len(arms) < 3 or (selected is not None and nid not in selected):
            continue
        node = nodes[nid]
        sid, node_tags, evidence_issue = _source_evidence(node, arms, junctions, ways)
        identity = "|".join([str(sid) if sid is not None else nid, *sorted(e["id"] for e in arms)])
        candidate = {"id": "auto-junction-" + hashlib.sha256(identity.encode()).hexdigest()[:12],
                     "rawNodeIds": [nid], "sourceNodeId": sid, "centerMeters": list(_xy(node)),
                     "shape": None, "status": "pending", "reason": None, "proposal": None,
                     "approaches": []}
        reason = evidence_issue
        if nid in protected:
            candidate.update(status="preserved", reason="existing_model_preserved")
            result.append(candidate)
            continue
        if len(arms) > 4:
            reason = "complex_degree"
        elif node.get("boundaryClipped") or node.get("sourceIncomplete"):
            reason = "source_topology_incomplete"
        elif not reason:
            reason = _restriction(arms, node_tags)
        if not reason and any(e["id"] in divided for e in arms):
            reason = "divided_road_unsupported"
        if not reason and all(e["kind"] == "roadLocal" for e in arms):
            candidate.update(status="preserved", reason="shared_junction_preserved")
            result.append(candidate)
            continue
        if not reason and sum(e["kind"] != "roadMajor" for e in arms) > 1:
            reason = "multiple_shared_approaches"
        if not reason:
            traces = [_trace_arm(nid, edge, nodes, adjacency, semantic_nodes) for edge in arms]
            candidate["approaches"] = traces
            reason = next((t["reason"] for t in traces if t["reason"]), None)
            if not reason:
                traces.sort(key=lambda t: (t["angleDeg"], t["edgeId"]))
                candidate["shape"], reason = _classify([t["angleDeg"] for t in traces])
                for trace in traces:
                    edge = next(e for e in arms if e["id"] == trace["edgeId"])
                    other = edge["to"] if edge["from"] == nid else edge["from"]
                    if math.dist(_xy(node), _xy(nodes[other])) * 0.35 < 1:
                        reason = reason or "template_approach_too_short"
                    elif _delta(_bearing(_xy(node), _xy(nodes[other])), trace["angleDeg"]) > STABILITY_DEGREES:
                        reason = reason or "template_direction_mismatch"
        candidate["reason"] = reason
        if not reason:
            candidate["status"] = "ready"
            candidate["proposal"] = {"id": candidate["id"], "rawNodeIds": [nid],
                "centerMeters": candidate["centerMeters"][:], "setbackMeters": 12,
                "verificationStatus": "geometry_inferred_unverified", "source": "普通路口自动识别 v1",
                "approachGroups": [{"id": "arm-" + hashlib.sha256(t["edgeId"].encode()).hexdigest()[:10],
                                    "edgeIds": [t["edgeId"]]} for t in candidate["approaches"]]}
        result.append(candidate)
    return result
