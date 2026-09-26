"""Convert the SVG preview graph to a *synthetic* v2 walking-network fixture.

This is an integration fixture, not a pedestrian-access or crossing survey.  In
particular, the source's roadMajor/roadLocal/roadPath labels are cartographic
classes, not proof that any individual edge is walkable.
"""

from collections import defaultdict
import math
from typing import Any


SIDEWALK_OFFSET_METERS = 3.0
SHARED_WIDTH_METERS = {"roadLocal": 4.0, "roadPath": 2.0}


def _xy(node: dict[str, Any]) -> tuple[float, float]:
    # The preview SVG uses south-positive Y; engine v2 uses north-positive Y.
    return float(node["x"]), -float(node["y"])


def _point(node: dict[str, Any]) -> list[float]:
    return list(_xy(node))


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def convert_preview_graph(graph: dict[str, Any], origin_wgs84: list[float]
                          ) -> dict[str, Any]:
    """Return a v2 network file loadable by backend/app/network.py.

    Major roads get separated, offset left/right sidewalk lines.  Local roads
    and paths become shared-way *assumptions*.  At a major-road vertex each
    shared branch gets its own endpoint, preventing the centreline junction
    from becoming a free crossing.  An inferred junction remains explicitly
    synthetic and is mapped to a turn or a charged crossing when it connects
    opposite sides of the same major source way.
    """
    if (graph.get("schemaVersion") != 1 or graph.get("kind") != "synthetic-road-graph"
            or graph.get("coordinateSystem") != "preview-local-v1"):
        raise ValueError("expected the synthetic preview-local-v1 road graph")
    if len(origin_wgs84) != 2 or not all(math.isfinite(v) for v in origin_wgs84):
        raise ValueError("origin_wgs84 must be a finite [lng, lat] pair")
    source_nodes = {node["id"]: node for node in graph["nodes"]}
    if len(source_nodes) != len(graph["nodes"]):
        raise ValueError("duplicate preview node ID")
    source_edges = graph["edges"]
    if len({edge["id"] for edge in source_edges}) != len(source_edges):
        raise ValueError("duplicate preview edge ID")
    for edge in source_edges:
        if edge["from"] not in source_nodes or edge["to"] not in source_nodes:
            raise ValueError("preview edge refers to a missing node")
        if edge["kind"] not in (*SHARED_WIDTH_METERS, "roadMajor", "inferredJunction"):
            raise ValueError(f"unsupported preview edge kind: {edge['kind']}")

    major_edges = [edge for edge in source_edges if edge["kind"] == "roadMajor"]
    major_at_node: dict[str, set[int]] = defaultdict(set)
    normals: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(list)
    for edge in major_edges:
        a, b = _xy(source_nodes[edge["from"]]), _xy(source_nodes[edge["to"]])
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 0.05:
            raise ValueError(f"degenerate major road edge: {edge['id']}")
        way = edge["sourceWayId"]
        normal = (-dy / length, dx / length)
        for node_id in (edge["from"], edge["to"]):
            major_at_node[node_id].add(way)
            normals[(way, node_id)].append(normal)

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    sidewalk_nodes: dict[tuple[int, str, str], str] = {}
    for (way, node_id), vectors in normals.items():
        nx, ny = (sum(vector[0] for vector in vectors),
                  sum(vector[1] for vector in vectors))
        magnitude = math.hypot(nx, ny)
        if magnitude < 0.1:
            nx, ny = vectors[0]
            magnitude = 1.0
        x, y = _xy(source_nodes[node_id])
        for side, sign in (("left", 1), ("right", -1)):
            out_id = f"major:{way}:{node_id}:{side}"
            sidewalk_nodes[(way, node_id, side)] = out_id
            nodes[out_id] = {
                "id": out_id,
                "xMeters": round(x + sign * SIDEWALK_OFFSET_METERS * nx / magnitude, 3),
                "yMeters": round(y + sign * SIDEWALK_OFFSET_METERS * ny / magnitude, 3),
            }

    # OSM frequently ends one way and starts another on the *same* continuous
    # street. Join only sidewalk points that land almost on top of each other
    # at that shared vertex; perpendicular roads remain separate.
    for node_id, ways in major_at_node.items():
        representatives: list[str] = []
        for way in sorted(ways):
            for side in ("left", "right"):
                candidate = sidewalk_nodes[(way, node_id, side)]
                point = [nodes[candidate]["xMeters"], nodes[candidate]["yMeters"]]
                match = next((other for other in representatives
                              if _distance(point, [nodes[other]["xMeters"],
                                                   nodes[other]["yMeters"]]) <= 0.75), None)
                if match is None:
                    representatives.append(candidate)
                else:
                    sidewalk_nodes[(way, node_id, side)] = match

    for edge in major_edges:
        way = edge["sourceWayId"]
        for side in ("left", "right"):
            from_id = sidewalk_nodes[(way, edge["from"], side)]
            to_id = sidewalk_nodes[(way, edge["to"], side)]
            a, b = nodes[from_id], nodes[to_id]
            path = [[a["xMeters"], a["yMeters"]], [b["xMeters"], b["yMeters"]]]
            if _distance(*path) < 0.05:
                raise ValueError(f"offset sidewalk collapsed: {edge['id']}:{side}")
            edges.append({
                "id": f"{edge['id']}:{side}", "from": from_id, "to": to_id,
                "kind": "sidewalk", "streetBlockId": f"major:{way}",
                "side": side, "pathMeters": path,
            })

    def shared_endpoint(edge: dict[str, Any], node_id: str) -> str:
        # A road crossing the major centreline must not silently join its two
        # branches.  Each incident edge has a distinct stub on that vertex.
        out_id = (f"shared:{node_id}:{edge['id']}" if node_id in major_at_node
                  else f"shared:{node_id}")
        if out_id not in nodes:
            x, y = _xy(source_nodes[node_id])
            nodes[out_id] = {"id": out_id, "xMeters": x, "yMeters": y}
        return out_id

    def location(node_id: str) -> list[float]:
        node = nodes[node_id]
        return [node["xMeters"], node["yMeters"]]

    def nearest_side(node_id: str, toward: list[float]) -> str:
        candidates = [sidewalk_nodes[(way, node_id, side)]
                      for way in sorted(major_at_node[node_id])
                      for side in ("left", "right")]
        center = _point(source_nodes[node_id])
        # The branch's direction determines which side of the road it meets.
        probe = [center[0] + 2 * (toward[0] - center[0]),
                 center[1] + 2 * (toward[1] - center[1])]
        return min(candidates, key=lambda candidate: (_distance(location(candidate), probe),
                                                       candidate))

    synthetic_link_ids: list[str] = []
    attached_sides: dict[tuple[int, str], set[str]] = defaultdict(set)
    for edge in source_edges:
        if edge["kind"] not in SHARED_WIDTH_METERS:
            continue
        from_id = shared_endpoint(edge, edge["from"])
        to_id = shared_endpoint(edge, edge["to"])
        edges.append({
            "id": edge["id"], "from": from_id, "to": to_id,
            "kind": "shared_way", "streetBlockId": f"shared:{edge['sourceWayId']}",
            "sharedWayType": "shared_alley", "widthMeters": SHARED_WIDTH_METERS[edge["kind"]],
            "pathMeters": [location(from_id), location(to_id)],
        })
        for endpoint, opposite in ((edge["from"], edge["to"]),
                                   (edge["to"], edge["from"])):
            if endpoint not in major_at_node:
                continue
            stub = shared_endpoint(edge, endpoint)
            side = nearest_side(endpoint, _point(source_nodes[opposite]))
            link_id = f"synthetic-turn:{edge['id']}:{endpoint}"
            edges.append({"id": link_id, "from": stub, "to": side,
                          "kind": "turn", "pathMeters": [location(stub), location(side)]})
            synthetic_link_ids.append(link_id)
            _, way, _, side_name = side.split(":", 3)
            attached_sides[(int(way), endpoint)].add(side_name.rsplit(":", 1)[-1])

    def inferred_endpoint(edge: dict[str, Any], node_id: str, other_id: str) -> str:
        if node_id in major_at_node:
            return nearest_side(node_id, _point(source_nodes[other_id]))
        return shared_endpoint(edge, node_id)

    synthetic_crossing_ids: list[str] = []
    for (way, node_id), sides in sorted(attached_sides.items()):
        if sides != {"left", "right"}:
            continue
        left = sidewalk_nodes[(way, node_id, "left")]
        right = sidewalk_nodes[(way, node_id, "right")]
        crossing_id = f"synthetic-crossing:{way}:{node_id}"
        edges.append({"id": crossing_id, "from": left, "to": right,
                      "kind": "crossing", "pathMeters": [location(left), location(right)]})
        synthetic_link_ids.append(crossing_id)
        synthetic_crossing_ids.append(crossing_id)
    for edge in source_edges:
        if edge["kind"] != "inferredJunction":
            continue
        from_id = inferred_endpoint(edge, edge["from"], edge["to"])
        to_id = inferred_endpoint(edge, edge["to"], edge["from"])
        if from_id == to_id:
            continue
        same_block_opposite = (from_id.startswith("major:") and to_id.startswith("major:")
                               and from_id.split(":", 2)[1] == to_id.split(":", 2)[1]
                               and from_id.rsplit(":", 1)[1] != to_id.rsplit(":", 1)[1])
        kind = "crossing" if same_block_opposite else "turn"
        edges.append({"id": edge["id"], "from": from_id, "to": to_id,
                      "kind": kind, "pathMeters": [location(from_id), location(to_id)]})
        synthetic_link_ids.append(edge["id"])
        if kind == "crossing":
            synthetic_crossing_ids.append(edge["id"])

    # Discard any offset nodes that could not be referenced by an edge.
    used = {node_id for edge in edges for node_id in (edge["from"], edge["to"])}
    nodes = {node_id: node for node_id, node in nodes.items() if node_id in used}
    if len({edge["id"] for edge in edges}) != len(edges):
        raise ValueError("converted network contains duplicate edge IDs")
    for edge in edges:
        if edge["from"] == edge["to"] or _distance(*edge["pathMeters"]) < 0.05:
            raise ValueError(f"converted network contains a degenerate edge: {edge['id']}")

    selection = [[round(x, 3), round(-y, 3)] for x, y in graph["selectionBoundary"][0]]
    xs, ys = zip(*selection)
    return {
        "schemaVersion": 2,
        "synthetic": True,
        "syntheticPurpose": "Python-C++-frontend integration only; not a verified walking network",
        "originWgs84": {"lng": origin_wgs84[0], "lat": origin_wgs84[1]},
        "supportedCenterBoundsMeters": {
            "minX": min(xs), "maxX": max(xs), "minY": min(ys), "maxY": max(ys),
        },
        "supportedCenterPolygonMeters": selection,
        "nodes": list(nodes.values()), "edges": edges,
        "facilities": [], "serviceCategories": [],
        "sourceGraph": {
            "nodes": len(graph["nodes"]), "edges": len(source_edges),
            "unverifiedInferredJunctions": len(graph.get("inferredJunctions", [])),
            "syntheticLinkIds": synthetic_link_ids,
            "syntheticCrossingIds": synthetic_crossing_ids,
        },
    }
