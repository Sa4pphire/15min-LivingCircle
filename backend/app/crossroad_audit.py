"""Inventory existing source junctions and audit their pedestrian semantics.

No geometric intersection becomes a connection. Candidate grouping uses only
existing source edges; proposals remain synthetic and are NOT applied here.
"""

from collections import Counter, defaultdict
import hashlib
import math
from typing import Any

from .junction_annotations import expand_reviewed_junction


def group_directions(entries: list[dict], tolerance: float = 25) -> list[list[dict]]:
    """Circular, bounded-span groups; avoid chaining several nearby forks."""
    ordered = sorted(entries, key=lambda e: e["angle"] % 360)
    if not ordered:
        return []
    gaps = [(ordered[(i+1) % len(ordered)]["angle"] - e["angle"]) % 360
            for i, e in enumerate(ordered)]
    start = (max(range(len(gaps)), key=gaps.__getitem__) + 1) % len(ordered)
    ordered = ordered[start:] + ordered[:start]
    groups = [[ordered[0]]]
    for entry in ordered[1:]:
        span = (entry["angle"] - groups[-1][0]["angle"]) % 360
        if span <= tolerance:
            groups[-1].append(entry)
        else:
            groups.append([entry])
    return groups


def _ray_angle(group: list[dict]) -> float:
    dx = sum(math.cos(math.radians(p["angle"])) for p in group)
    dy = sum(math.sin(math.radians(p["angle"])) for p in group)
    return math.degrees(math.atan2(dy, dx)) % 360


def _four_arm_shape(groups: list[list[dict]]) -> bool:
    if len(groups) != 4:
        return False
    angles = sorted(_ray_angle(g) for g in groups)
    gaps = [(angles[(i+1) % 4] - angles[i]) % 360 for i in range(4)]
    return min(gaps) >= 35 and max(gaps) <= 145


def discover_crossroads(source: dict[str, Any]) -> list[dict]:
    """Catalogue four-arm nodes/clusters, not an assertion of public access.

    Short, already-connected carriageway junction vertices may be one physical
    junction. Never group merely nearby vertices, inferred links, or a chain
    whose extent exceeds the corner model. Unclear configurations stay visible.
    """
    nodes = {n["id"]: n for n in source["nodes"]}
    incidence: dict[str, list[dict]] = defaultdict(list)
    for edge in source["edges"]:
        if edge["kind"] != "inferredJunction":
            incidence[edge["from"]].append(edge)
            incidence[edge["to"]].append(edge)
    anchors = {nid for nid, edges in incidence.items() if len(edges) >= 3 and
               any(e["kind"] == "roadMajor" for e in edges)}
    parent = {nid: nid for nid in anchors}

    def root(nid: str) -> str:
        while parent[nid] != nid:
            parent[nid] = parent[parent[nid]]
            nid = parent[nid]
        return nid

    def xy(nid: str) -> list[float]:
        return [nodes[nid]["x"], -nodes[nid]["y"]]

    # Only existing, direct short edges. Intermediate/complex junction geometry
    # is intentionally left for explicit review rather than bridge assumptions.
    for edge in source["edges"]:
        a, b = edge["from"], edge["to"]
        if (edge["kind"] != "inferredJunction" and a in anchors and b in anchors
                and math.dist(xy(a), xy(b)) <= 30):
            parent[root(a)] = root(b)
    clusters: dict[str, set[str]] = defaultdict(set)
    for nid in anchors:
        clusters[root(nid)].add(nid)
    # A degree-two merge/loop *inside* an already connected junction can make
    # two external approach stubs meet and bypass a crossing. Absorb it only
    # when BOTH existing neighbours belong to that same compact cluster.
    # Never add a nearby but disconnected node or merge two separate clusters.
    for members in clusters.values():
        changed = True
        while changed:
            changed = False
            center = [sum(xy(n)[i] for n in members)/len(members) for i in (0, 1)]
            for nid, incident in incidence.items():
                if nid in members or len(incident) != 2 or math.dist(xy(nid), center) > 20:
                    continue
                neighbours = [e["to"] if e["from"] == nid else e["from"] for e in incident]
                if len(set(neighbours)) == 2 and all(other in members for other in neighbours):
                    members.add(nid)
                    changed = True
    # Shared-way crossroads need connectivity, but no crossing wait or sidewalks.
    clusters.update({f"shared:{nid}": {nid} for nid, edges in incidence.items()
                     if len(edges) == 4 and nid not in anchors})
    result = []
    for raw_ids in clusters.values():
        center = [sum(xy(n)[i] for n in raw_ids) / len(raw_ids) for i in (0, 1)]
        outgoing = {e["id"]: e for n in raw_ids for e in incidence[n]
                    if (e["from"] in raw_ids) != (e["to"] in raw_ids)}
        entries = []
        for edge in outgoing.values():
            first = edge["from"] if edge["from"] in raw_ids else edge["to"]
            other = edge["to"] if first == edge["from"] else edge["from"]
            a, b = xy(first), xy(other)
            entries.append({"edgeId": edge["id"], "kind": edge["kind"],
                            "angle": math.degrees(math.atan2(b[1]-a[1], b[0]-a[0])) % 360})
        groups = group_directions(entries)
        # Keep raw four-way nodes even when they are a fork, offset junction or
        # too-large cluster; this makes omissions auditable rather than hidden.
        raw_four = any(len(incidence[n]) == 4 for n in raw_ids)
        if not raw_four and len(groups) != 4:
            continue
        raw_ids = sorted(raw_ids)
        candidate_id = "crossroad-" + hashlib.sha256("|".join(raw_ids).encode()).hexdigest()[:12]
        major = any(e["kind"] == "roadMajor" for e in outgoing.values())
        reason = None
        if max(math.dist(xy(n), center) for n in raw_ids) > 20:
            reason = "cluster_too_large"
        elif not _four_arm_shape(groups):
            reason = "ambiguous_or_not_four_arm"
        elif any(sum(e["kind"] != "roadMajor" for e in group) > 1 for group in groups) and major:
            reason = "multiple_shared_approaches_need_review"
        annotation = {"id": candidate_id, "name": f"十字路口 · {candidate_id[-6:]}",
                      "centerMeters": [round(p, 3) for p in center], "rawNodeIds": raw_ids,
                      "verificationStatus": "geometry_inferred_unverified",
                      "source": "2026-09-29 十字路口专项校对：已有原图节点及短连接的四向道路模型；横道与通行许可未核实。",
                      "mapEvidence": "原图道路几何与 SVG 局部校对；不是百度已确认的合法过街。",
                      "approachGroups": [{"id": f"arm-{i}", "edgeIds": sorted(e["edgeId"] for e in g)}
                                         for i, g in enumerate(sorted(groups, key=_ray_angle))]}
        result.append({"id": candidate_id, "rawNodeIds": raw_ids,
                       "centerMeters": annotation["centerMeters"], "ordinaryRoad": major,
                       "rawFourWayVertices": sum(len(incidence[n]) == 4 for n in raw_ids),
                       "approachCount": len(groups), "reason": reason,
                       "proposal": annotation if major and reason is None else None})
    return sorted(result, key=lambda r: (-r["centerMeters"][1], r["centerMeters"][0]))


def audit_explicit_junction(graph: dict, record: dict) -> dict:
    """Independently reconstruct corner/crossing pairs from actual port rays.

    Counts alone are insufficient: detect wrong-side turns, missing crossings,
    duplicate connectors, stale retired edges and hidden old-centre transfers.
    """
    by_id = {e["id"]: e for e in graph["edges"]}
    nodes = {n["id"]: n for n in graph["nodes"]}
    issues = []
    entries = []
    for port in record["ports"]:
        edge = by_id.get(port["edgeId"])
        nid = port["nodeId"]
        if edge is None or nid not in (edge["from"], edge["to"]):
            issues.append("missing_or_disconnected_approach")
            continue
        other = edge["to"] if edge["from"] == nid else edge["from"]
        a, b = nodes[nid], nodes[other]
        entries.append({"id": nid, "point": [a["xMeters"], a["yMeters"]],
                        "kind": edge["kind"],
                        "angle": math.degrees(math.atan2(b["yMeters"]-a["yMeters"],
                                                         b["xMeters"]-a["xMeters"])) % 360})
    if len({p["nodeId"] for p in record["ports"]}) != len(record["ports"]):
        issues.append("duplicate_ports")
    old_nodes = {p["endpointNodeId"] for p in record["ports"]}
    if any(e["from"] in old_nodes or e["to"] in old_nodes for e in graph["edges"]):
        issues.append("old_center_free_transfer")
    if set(record.get("retiredEdgeIds", [])) & by_id.keys():
        issues.append("retired_edge_still_active")
    terminal_names = record.get('terminalMedianPortIds', [])
    known_ports = {p['id']: p['nodeId'] for p in record['ports']}
    if (not isinstance(terminal_names, list) or
            any(not isinstance(name, str) for name in terminal_names) or
            len(set(terminal_names)) != len(terminal_names) or
            not set(terminal_names) <= known_ports.keys()):
        issues.append('invalid_terminal_median_ports')
        terminal_names = []
    terminal_nodes = {known_ports[name] for name in terminal_names}
    arms = sorted(group_directions(entries), key=_ray_angle)
    if len(arms) not in (3, 4):
        issues.append("ambiguous_port_directions")
        return {"id": record["id"], "pass": False, "issues": sorted(set(issues))}
    center = record["centerMeters"]
    for arm in arms:
        angle = math.radians(_ray_angle(arm))
        arm.sort(key=lambda p: math.cos(angle)*(p["point"][1]-center[1]) -
                              math.sin(angle)*(p["point"][0]-center[0]))
    expected = set()
    for i, arm in enumerate(arms):
        following = arms[(i+1) % len(arms)]
        expected.add(("turn", frozenset((arm[-1]["id"], following[0]["id"]))))
        if len(arm) > 1:
            expected.add(("crossing", frozenset((arm[0]["id"], arm[-1]["id"]))))
    paired = set()
    for i, arm in enumerate(arms):
        if len(arm) <= 2 or i in paired:
            continue
        opposite = min((j for j in range(len(arms)) if j != i),
                       key=lambda j: math.cos(math.radians(_ray_angle(arm)-_ray_angle(arms[j]))))
        middle, other_middle = arm[1:-1], arms[opposite][1:-1][::-1]
        if len(middle) != len(other_middle):
            if len(arms[opposite]) == 1 and arms[opposite][0]["kind"] == "shared_way":
                for port in middle:
                    expected.add(("crossing", frozenset((port["id"], arms[opposite][0]["id"]))))
            elif not other_middle and {p['id'] for p in middle} == terminal_nodes:
                # An explicitly closed inward approach can leave the opposite
                # median stubs as dead ends; do not invent a diagonal crossing.
                pass
            else:
                issues.append("unmatched_median_ports")
        else:
            for a, b in zip(middle, other_middle):
                expected.add(("crossing", frozenset((a["id"], b["id"]))))
        paired.update((i, opposite))
    actual = Counter()
    port_ids = {p["nodeId"] for p in record["ports"]}
    approach_ids = {p["edgeId"] for p in record["ports"]}
    for edge in graph["edges"]:
        incident = edge["from"] in port_ids or edge["to"] in port_ids
        internal = edge["from"] in port_ids and edge["to"] in port_ids
        if incident and not internal and edge["id"] not in approach_ids:
            issues.append("unexpected_port_connection")
        if edge["from"] not in port_ids or edge["to"] not in port_ids:
            continue
        if edge["from"] == edge["to"]:
            issues.append("degenerate_connector")
            continue
        actual[(edge["kind"], frozenset((edge["from"], edge["to"])))] += 1
        if edge["kind"] == "crossing":
            wait = edge.get("waitSeconds", 20)
            if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait) or wait < 0:
                issues.append("invalid_crossing_wait")
        elif "waitSeconds" in edge:
            issues.append("turn_has_wait")
    if set(actual) != expected:
        issues.append("missing_or_wrong_corner_crossing_pairs")
    if any(node_id in pair for _, pair in actual for node_id in terminal_nodes):
        issues.append('terminal_median_port_has_connector')
    if any(n != 1 for n in actual.values()):
        issues.append("duplicate_connectors")
    # Include approach stubs too: checking only corner connectors misses a
    # degree-two merge/triangle inside the junction that bypasses a crossing.
    adjacency = defaultdict(set)
    for kind, pair in actual:
        if kind != "crossing":
            a, b = tuple(pair)
            adjacency[a].add(b)
            adjacency[b].add(a)
    for edge in graph["edges"]:
        if edge["id"] in approach_ids and edge["kind"] != "crossing":
            adjacency[edge["from"]].add(edge["to"])
            adjacency[edge["to"]].add(edge["from"])
    for kind, pair in actual:
        if kind != "crossing":
            continue
        a, b = tuple(pair)
        reached, pending = {a}, [a]
        while pending:
            for target in adjacency[pending.pop()]:
                if target not in reached:
                    reached.add(target)
                    pending.append(target)
        if b in reached:
            issues.append("free_crossing_bypass")
    return {"id": record["id"], "pass": not issues, "issues": sorted(set(issues)),
            "approaches": len(arms), "ports": len(entries),
            "turns": sum(kind == "turn" for kind, _ in actual),
            "crossings": sum(kind == "crossing" for kind, _ in actual)}


def audit_crossroads(source: dict, graph: dict, baseline: dict) -> dict:
    """Read-only inventory; all uncertainty is retained in the result."""
    candidates = discover_crossroads(source)
    records = graph["sourceGraph"]["manualJunctionAnnotations"]
    checked = {r["id"]: audit_explicit_junction(graph, r) for r in records}
    base_nodes = {n["id"]: n for n in baseline["nodes"]}
    raw_ids = {n["id"] for n in source["nodes"]}
    for candidate in candidates:
        matched = [r for r in records if set(r.get("rawNodeIds", [])) & set(candidate["rawNodeIds"])]
        # Legacy explicit ports also identify original raw vertices exactly.
        if not matched:
            matched = [r for r in records if any(
                any(f":{n}:" in p["endpointNodeId"] or p["endpointNodeId"] == f"shared:{n}"
                    for p in r["ports"]) for n in candidate["rawNodeIds"] if n in raw_ids)]
        candidate["modelIds"] = [r["id"] for r in matched]
        if matched:
            covered = {n for r in matched for n in r.get("rawNodeIds", [])}
            covered.update(n for r in matched if "rawNodeIds" not in r for n in candidate["rawNodeIds"]
                           if any(f":{n}:" in p["endpointNodeId"] or p["endpointNodeId"] == f"shared:{n}"
                                  for p in r["ports"]))
            if covered and not set(candidate["rawNodeIds"]) <= covered:
                candidate["status"] = "partial_explicit_model"
            else:
                candidate["status"] = "explicit_model_pass" if all(checked[r["id"]]["pass"] for r in matched) else "explicit_model_failed"
            candidate["proposal"] = None
        elif candidate["reason"] is not None:
            candidate["status"] = "needs_manual_review"
        elif not candidate["ordinaryRoad"]:
            nid = "shared:" + candidate["rawNodeIds"][0]
            incident = [e for e in graph["edges"] if nid in (e["from"], e["to"])]
            candidate["status"] = "shared_junction_pass" if len(incident) == 4 and all(e["kind"] == "shared_way" for e in incident) else "shared_junction_failed"
        else:
            try:
                expanded = expand_reviewed_junction(candidate["proposal"], base_nodes, baseline["edges"])
                from .junction_annotations import apply_junction_annotations
                simulated_nodes = dict(base_nodes)
                simulated_edges, simulated_records = apply_junction_annotations(
                    simulated_nodes, baseline["edges"], [expanded])
                check = audit_explicit_junction({"nodes": list(simulated_nodes.values()),
                                                "edges": simulated_edges}, simulated_records[0])
                if not check["pass"]:
                    raise ValueError("approach/corner rules need review: " + ", ".join(check["issues"]))
            except (ValueError, KeyError) as error:
                candidate["reason"] = str(error)
                candidate["status"] = "needs_manual_review"
                candidate["proposal"] = None
            else:
                candidate["status"] = "missing_explicit_model"
    return {"schemaVersion": 1, "synthetic": True, "coordinateSystem": "engine-local-meters",
            "scope": "Existing four-arm source topology, not public-access or grade-separation verification",
            "rawFourWayVertexCount": sum(c["rawFourWayVertices"] for c in candidates),
            "candidateCount": len(candidates), "statuses": dict(Counter(c["status"] for c in candidates)),
            "explicitJunctionChecks": list(checked.values()), "crossroads": candidates,
            "limitations": ["Disconnected geometric crossings are never auto-connected.",
                            "Ambiguous forks, large clusters and unmatched lanes require manual review.",
                            "All inferred crossings remain unverified; map geometry does not prove legal access."]}
