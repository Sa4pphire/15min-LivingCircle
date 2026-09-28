"""Explicit, reproducible sidewalk junction repairs for synthetic data only."""

import math
from typing import Any


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
        if annotation.get("verificationStatus") != "user_marked_unverified":
            raise ValueError("image-marked junctions must remain unverified")
        if not isinstance(annotation.get("source"), str) or not annotation["source"]:
            raise ValueError("junction annotation needs a source")
        center = annotation.get("centerMeters")
        if (not isinstance(center, list) or len(center) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                    not math.isfinite(v) for v in center)):
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
            if (edge is None or edge["kind"] != "sidewalk" or
                    endpoint not in (edge["from"], edge["to"]) or
                    edge_id in touched_edges):
                raise ValueError(f"invalid junction sidewalk port: {edge_id}")
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
    return list(by_id.values()), records
