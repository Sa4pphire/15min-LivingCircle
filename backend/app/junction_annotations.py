"""Explicit, reproducible sidewalk junction repairs for synthetic data only."""

import math
from typing import Any


UNVERIFIED_STATUSES = {"user_marked_unverified", "map_reviewed_unverified",
                       "geometry_inferred_unverified"}


def _finite_point(value: Any) -> bool:
    return (isinstance(value, list) and len(value) == 2 and
            all(not isinstance(v, bool) and isinstance(v, (int, float)) and
                math.isfinite(v) for v in value))


def expand_reviewed_junction(
    annotation: dict[str, Any], nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expand *explicitly selected* approach groups into sidewalk corner ports.

    This is not an intersection detector. A reviewer must choose the raw nodes
    and each physical approach. Parallel carriageways belong to one approach;
    only its outer sidewalks turn around corners. Median sidewalks continue
    across the side street through charged crossings, never free turn links.
    """
    center = annotation.get("centerMeters")
    if not _finite_point(center):
        raise ValueError("junction centerMeters must be a finite point")
    if annotation.get("verificationStatus") not in UNVERIFIED_STATUSES:
        raise ValueError("reviewed junctions must remain unverified")
    by_id = {edge["id"]: edge for edge in edges}
    raw_nodes = annotation.get("rawNodeIds")
    groups = annotation.get("approachGroups")
    if (not isinstance(raw_nodes, list) or not raw_nodes or
            any(not isinstance(n, str) or not n for n in raw_nodes) or
            len(set(raw_nodes)) != len(raw_nodes) or not isinstance(groups, list) or
            len(groups) not in (3, 4) or
            any(not isinstance(group, dict) or not isinstance(group.get("id"), str) or
                not group["id"] or not isinstance(group.get("edgeIds"), list) or
                not group["edgeIds"] or
                any(not isinstance(e, str) or not e for e in group["edgeIds"]) for group in groups) or
            len({group["id"] for group in groups}) != len(groups)):
        raise ValueError("reviewed junction needs unique raw nodes and 3 or 4 approaches")
    requested_setback = annotation.get("setbackMeters", 12)
    if (isinstance(requested_setback, bool) or not isinstance(requested_setback, (int, float)) or
            not math.isfinite(requested_setback) or requested_setback < 1):
        raise ValueError("reviewed setback must be a finite distance of at least one meter")

    def location(node_id: str) -> list[float]:
        node = nodes[node_id]
        return [node["xMeters"], node["yMeters"]]

    def original_node(node_id: str) -> bool:
        return any(node_id == f"shared:{raw}" or f":{raw}:" in node_id
                   for raw in raw_nodes)

    selected: set[str] = set()
    ports: list[dict[str, Any]] = []
    arms: list[dict[str, Any]] = []
    for group in groups:
        candidates: list[dict[str, Any]] = []
        directions: list[list[float]] = []
        for raw_edge_id in group["edgeIds"]:
            ids = ([raw_edge_id] if raw_edge_id in by_id else
                   [f"{raw_edge_id}:left", f"{raw_edge_id}:right"])
            for edge_id in ids:
                edge = by_id.get(edge_id)
                if edge is None or edge["kind"] not in ("sidewalk", "shared_way"):
                    raise ValueError(f"unknown reviewed approach: {edge_id}")
                if edge_id in selected:
                    raise ValueError("reviewed approach edge selected twice")
                endpoints = [nid for nid in (edge["from"], edge["to"]) if original_node(nid)]
                if len(endpoints) != 1:
                    raise ValueError("reviewed approach must leave the explicitly selected junction")
                endpoint = endpoints[0]
                other = edge["to"] if endpoint == edge["from"] else edge["from"]
                a, b = location(endpoint), location(other)
                length = math.dist(a, b)
                if not math.isfinite(length) or length < 0.05:
                    raise ValueError("reviewed approach is degenerate")
                setback = min(requested_setback, length * 0.35)
                if setback < 1 or math.dist(a, center) > 25:
                    raise ValueError("reviewed approach is too short or outside junction")
                direction = [(b[i] - a[i]) / length for i in (0, 1)]
                point = [a[i] + setback * direction[i] for i in (0, 1)]
                port_id = f"{group['id']}-{len(candidates)}"
                port = {"id": port_id, "edgeId": edge_id, "endpointNodeId": endpoint,
                        "setbackMeters": round(setback, 6)}
                candidates.append({"id": port_id, "point": point, "kind": edge["kind"]})
                directions.append(direction)
                ports.append(port)
                selected.add(edge_id)
        dx = sum(d[0] for d in directions)
        dy = sum(d[1] for d in directions)
        magnitude = math.hypot(dx, dy)
        if magnitude < 0.1:
            raise ValueError("an approach cannot contain opposite road directions")
        ray = [dx / magnitude, dy / magnitude]
        if any(sum(ray[i] * d[i] for i in (0, 1)) < math.cos(math.radians(25))
               for d in directions):
            raise ValueError("approach group is not a parallel carriageway")
        candidates.sort(key=lambda p: ray[0] * (p["point"][1] - center[1]) -
                        ray[1] * (p["point"][0] - center[0]))
        arms.append({"id": group["id"], "ray": ray, "ports": candidates,
                     "angle": math.atan2(ray[1], ray[0])})
    arms.sort(key=lambda arm: arm["angle"])

    retired = []
    for edge in edges:
        if edge["id"] in selected:
            continue
        if original_node(edge["from"]) or original_node(edge["to"]):
            if any(math.dist(p, center) > 25 for p in edge["pathMeters"]):
                raise ValueError(f"unhandled approach at reviewed junction: {edge['id']}")
            retired.append(edge["id"])

    connectors = []
    for index, arm in enumerate(arms):
        following = arms[(index + 1) % len(arms)]
        gap = (following["angle"] - arm["angle"]) % (2 * math.pi)
        if gap < math.radians(25) or gap > math.radians(195):
            raise ValueError("reviewed junction has overlapping or missing approaches")
        connectors.append({"id": f"corner-{arm['id']}-{following['id']}", "kind": "turn",
                           "fromPort": arm["ports"][-1]["id"],
                           "toPort": following["ports"][0]["id"]})
        if len(arm["ports"]) > 1:
            connectors.append({"id": f"cross-{arm['id']}", "kind": "crossing",
                               "fromPort": arm["ports"][0]["id"],
                               "toPort": arm["ports"][-1]["id"], "waitSeconds": 20})
    median_connections = annotation.get("medianConnections", [])
    if (not isinstance(median_connections, list) or
            any(not isinstance(c, dict) or not isinstance(c.get("id"), str) or
                not c["id"] for c in median_connections) or
            len({c["id"] for c in median_connections}) != len(median_connections)):
        raise ValueError("median connections need unique explicit IDs")
    used_median_connections: set[str] = set()
    paired: set[str] = set()
    for arm in arms:
        middle = arm["ports"][1:-1]
        if not middle or arm["id"] in paired:
            continue
        opposite = min((other for other in arms if other is not arm),
                       key=lambda other: sum(arm["ray"][i] * other["ray"][i] for i in (0, 1)))
        dot = sum(arm["ray"][i] * opposite["ray"][i] for i in (0, 1))
        other_middle = opposite["ports"][1:-1][::-1]
        if dot > -math.cos(math.radians(30)):
            raise ValueError("median sidewalks need explicitly matching opposite approaches")
        if len(middle) != len(other_middle):
            # Two incoming carriageways may continue into one shared road.
            # This is NOT inferred automatically: require an explicit charged
            # connection for each middle port to the opposite shared entrance.
            if len(opposite["ports"]) != 1 or opposite["ports"][0]["kind"] != "shared_way":
                raise ValueError("median sidewalks need explicitly matching opposite approaches")
            target = opposite["ports"][0]["id"]
            selected_links = [c for c in median_connections
                              if c.get("fromPort") in {p["id"] for p in middle}]
            if (len(selected_links) != len(middle) or
                    {c.get("fromPort") for c in selected_links} != {p["id"] for p in middle} or
                    any(c.get("toPort") != target for c in selected_links)):
                raise ValueError("asymmetric median needs explicit crossings to the opposite shared way")
            for link in selected_links:
                connectors.append({**link, "kind": "crossing", "waitSeconds": link.get("waitSeconds", 20)})
                used_median_connections.add(link["id"])
            paired.update((arm["id"], opposite["id"]))
            continue
        for index, (first, second) in enumerate(zip(middle, other_middle)):
            connectors.append({"id": f"median-{arm['id']}-{opposite['id']}-{index}",
                               "kind": "crossing", "fromPort": first["id"],
                               "toPort": second["id"], "waitSeconds": 20})
        paired.update((arm["id"], opposite["id"]))
    if used_median_connections != {c["id"] for c in median_connections}:
        raise ValueError("unused or misplaced explicit median connection")
    return {**annotation, "ports": ports, "connectors": connectors,
            "retireEdgeIds": sorted(retired)}


def validate_inferred_junction_annotations(
    annotations: list[dict[str, Any]],
    source_edges: list[dict[str, Any]],
) -> None:
    """Validate review notes for connections already inferred by the graph."""
    edge_ids = {edge.get("id") for edge in source_edges}
    seen_ids: set[str] = set()
    for annotation in annotations:
        name = annotation.get("id")
        if not isinstance(name, str) or not name or name in seen_ids:
            raise ValueError("inferred junction annotation needs a unique ID")
        seen_ids.add(name)
        if annotation.get("verificationStatus") != "geometry_inferred_unverified":
            raise ValueError("inferred junctions must remain unverified")
        if not isinstance(annotation.get("source"), str) or not annotation["source"]:
            raise ValueError("inferred junction annotation needs a source")
        center = annotation.get("centerMeters")
        if (not isinstance(center, list) or len(center) != 2 or
                any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) for value in center)):
            raise ValueError("inferred junction centerMeters must be a finite point")
        groups = annotation.get("approachGroups")
        if not isinstance(groups, list) or not groups:
            raise ValueError("inferred junction needs approach groups")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("id"), str):
                raise ValueError("inferred junction approach group needs an ID")
            edge_group = group.get("edgeIds")
            if (not isinstance(edge_group, list) or not edge_group or
                    any(edge_id not in edge_ids for edge_id in edge_group)):
                raise ValueError("inferred junction approach has an unknown edge")


def apply_junction_annotations(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cut approaches back into distinct ports and add labelled connectors.

    Keeping the original shared centre node would allow crossing the side
    street for free. Every approach endpoint therefore becomes its own port.
    Only the annotation's turn/crossing edges can connect those ports.
    """
    by_id = {edge["id"]: edge for edge in edges}
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def location(node_id: str) -> list[float]:
        node = nodes[node_id]
        return [node["xMeters"], node["yMeters"]]

    for annotation in annotations:
        name = annotation.get("id")
        if not isinstance(name, str) or not name or name in seen_ids:
            raise ValueError("junction annotation needs a unique ID")
        seen_ids.add(name)
        if annotation.get("verificationStatus") not in UNVERIFIED_STATUSES:
            raise ValueError("reviewed junctions must remain unverified")
        if not isinstance(annotation.get("source"), str) or not annotation["source"]:
            raise ValueError("junction annotation needs a source")
        center = annotation.get("centerMeters")
        if not _finite_point(center):
            raise ValueError("junction centerMeters must be a finite point")
        if not isinstance(annotation.get("ports"), list) or not annotation["ports"]:
            raise ValueError("junction needs explicitly marked approach ports")
        if not isinstance(annotation.get("connectors"), list) or not annotation["connectors"]:
            raise ValueError("junction needs explicit connectors")
        retired = annotation.get("retireEdgeIds", [])
        if not isinstance(retired, list) or len(set(retired)) != len(retired):
            raise ValueError("invalid retired junction edges")
        for edge_id in retired:
            edge = by_id.get(edge_id)
            if edge is None:
                raise ValueError(f"unknown retired junction edge: {edge_id}")
            if any(math.dist(point, center) > 25 for point in edge["pathMeters"]):
                raise ValueError("retired edges must stay inside the marked junction")
            del by_id[edge_id]

        ports: dict[str, str] = {}
        port_records: list[dict[str, Any]] = []
        touched_edges: set[str] = set()
        for port in annotation["ports"]:
            port_id = port.get("id")
            edge_id = port.get("edgeId")
            edge = by_id.get(edge_id)
            endpoint = port.get("endpointNodeId")
            setback = port.get("setbackMeters", 18)
            if not isinstance(port_id, str) or not port_id or port_id in ports:
                raise ValueError("junction port IDs must be unique")
            if (edge is None or edge["kind"] not in ("sidewalk", "shared_way") or
                    endpoint not in (edge["from"], edge["to"]) or
                    edge_id in touched_edges):
                raise ValueError(f"invalid junction walkway port: {edge_id}")
            if (isinstance(setback, bool) or not isinstance(setback, (int, float)) or
                    not math.isfinite(setback) or setback < 1):
                raise ValueError("junction setback must be at least one meter")
            if len(edge["pathMeters"]) != 2:
                raise ValueError("preview junction approaches must be straight segments")
            first = endpoint == edge["from"]
            a, b = edge["pathMeters"] if first else edge["pathMeters"][::-1]
            length = math.dist(a, b)
            if math.dist(a, center) > 25 or setback >= length - 0.05:
                raise ValueError("junction setback exceeds its marked approach")
            point = [round(a[i] + (b[i] - a[i]) * setback / length, 3) for i in (0, 1)]
            if math.dist(point, center) > 50:
                raise ValueError("junction port lies outside the marked junction")
            node_id = f"manual-junction:{name}:port:{port_id}"
            if node_id in nodes:
                raise ValueError(f"duplicate junction node ID: {node_id}")
            nodes[node_id] = {"id": node_id, "xMeters": point[0], "yMeters": point[1]}
            ports[port_id] = node_id
            touched_edges.add(edge_id)
            path = [point, b] if first else [b, point]
            by_id[edge_id] = {**edge, "from": node_id if first else edge["from"],
                              "to": edge["to"] if first else node_id, "pathMeters": path}
            port_records.append({**port, "nodeId": node_id})

        # No old centre node may preserve a hidden free transfer after cutting
        # back an approach. All other incident edges must be cut or retired.
        endpoints = {port["endpointNodeId"] for port in port_records}
        if any(edge["from"] in endpoints or edge["to"] in endpoints
               for edge in by_id.values()):
            raise ValueError("junction repair leaves an unhandled original centre connection")

        link_records: list[dict[str, Any]] = []
        for connector in annotation["connectors"]:
            label = connector.get("id")
            kind = connector.get("kind")
            start = ports.get(connector.get("fromPort"))
            end = ports.get(connector.get("toPort"))
            if (not isinstance(label, str) or not label or kind not in ("turn", "crossing")
                    or start is None or end is None or start == end):
                raise ValueError("invalid explicit junction connector")
            edge_id = f"manual-junction:{name}:{label}"
            if edge_id in by_id:
                raise ValueError(f"duplicate junction connector: {edge_id}")
            if math.dist(location(start), location(end)) < 0.05:
                raise ValueError("junction connector is degenerate")
            edge = {"id": edge_id, "from": start, "to": end, "kind": kind,
                    "pathMeters": [location(start), location(end)],
                    "annotationId": name, "annotationKind": "junction",
                    "verificationStatus": annotation["verificationStatus"],
                    "annotationSource": annotation["source"]}
            if kind == "crossing":
                wait = connector.get("waitSeconds", 20)
                if (isinstance(wait, bool) or not isinstance(wait, (int, float)) or
                        not math.isfinite(wait) or wait < 0):
                    raise ValueError("junction crossing wait must be non-negative")
                edge["waitSeconds"] = float(wait)
            elif "waitSeconds" in connector:
                raise ValueError("same-corner turns cannot carry crossing wait")
            by_id[edge_id] = edge
            link_records.append({**connector, "edgeId": edge_id})
        records.append({"id": name, "centerMeters": center,
                        "verificationStatus": annotation["verificationStatus"],
                        "source": annotation["source"], "retiredEdgeIds": retired,
                        "ports": port_records, "connectors": link_records})
        for key in ("name", "rawNodeIds", "approachGroups", "mapEvidence", "medianConnections"):
            if key in annotation:
                records[-1][key] = annotation[key]
    return list(by_id.values()), records
