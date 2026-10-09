"""Convert the SVG preview graph to a *synthetic* v2 walking-network fixture.

This is an integration fixture, not a pedestrian-access or crossing survey.  In
particular, the source's roadMajor/roadLocal/roadPath labels are cartographic
classes, not proof that any individual edge is walkable.
"""

from collections import defaultdict
import math
from typing import Any

from .junction_annotations import (
    apply_junction_annotations,
    expand_reviewed_junction,
    validate_inferred_junction_annotations,
)
from .divided_road_sections import apply_divided_road_sections
from .preview_connections import apply_preview_connections
from .major_sidewalks import apply_major_sidewalk_policy
from .walking_types import normalize_graph


SIDEWALK_OFFSET_METERS = 3.0
SHARED_WIDTH_METERS = {"roadLocal": 4.0, "roadPath": 2.0}


def _coalesce_source_crossings(edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep one wait across shape vertices, but never erase an actual branch.

    A branch in the middle of a tagged crossing needs island/side review. Until
    then each distinct leg stays charged, rather than inventing a free transfer.
    """
    incident = defaultdict(list)
    for edge in edges:
        for endpoint in (edge['from'], edge['to']):
            incident[endpoint].append(edge)
    tagged = {e['id']: e for e in edges if e.get('sourceCrossing')}
    remaining = dict(tagged)
    output, records = [], []
    while remaining:
        first = remaining.pop(min(remaining))
        path = first['pathMeters'][:]
        start, end = first['from'], first['to']
        ids = [first['id']]
        for reverse in (False, True):
            if reverse:
                start, end, path = end, start, path[::-1]
            while len(incident[end]) == 2:
                candidate = next((e for e in incident[end] if e['id'] in remaining and
                                  e.get('sourceWayId') == first['sourceWayId']), None)
                if candidate is None:
                    break
                remaining.pop(candidate['id'])
                segment = candidate['pathMeters']
                if candidate['to'] == end:
                    segment = segment[::-1]
                    end = candidate['from']
                else:
                    end = candidate['to']
                path.extend(segment[1:])
                ids.append(candidate['id'])
        if start == end:
            raise ValueError('source crossing must not be a closed loop')
        new_id = first['id'] if len(ids) == 1 else min(ids) + ':crossing-chain'
        branch = any(len(incident[n]) > 2 and any(e.get('sourceCrossing') and
                     e.get('sourceWayId') == first['sourceWayId'] and e['id'] not in ids
                     for e in incident[n]) for n in (start, end))
        output.append({**first, 'id': new_id, 'from': start, 'to': end, 'pathMeters': path,
                       'sourceEdgeIds': sorted(ids),
                       'crossingReviewRequired': branch})
        records.append({'edgeId': new_id, 'sourceWayId': first['sourceWayId'],
                        'sourceEdgeIds': sorted(ids), 'branchReviewRequired': branch,
                        'waitPolicy': 'engine_default_per_leg',
                        'verificationStatus': 'online_source_unverified_on_site'})
    return [e for e in edges if e['id'] not in tagged] + output, records


def with_preview_annotations(graph: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    """The annotation file is authoritative; source metadata is a generated copy."""
    if (annotations.get("schemaVersion") != 1 or annotations.get("synthetic") is not True or
            annotations.get("coordinateSystem") != "engine-local-meters" or
            not isinstance(annotations.get("crossings"), list) or
            not isinstance(annotations.get("junctions", []), list)):
        raise ValueError("invalid synthetic annotation file")
    sections = annotations.get("dividedRoadSections", graph.get("dividedRoadSections", []))
    connections = annotations.get("connections", [])
    if (not isinstance(sections, list) or not isinstance(connections, list) or
            ("dividedRoadSections" in annotations and
             annotations.get("dividedRoadSectionsCoordinateSystem") != "preview-local-v1")):
        raise ValueError("invalid divided-road annotation coordinate system or lists")
    return {**graph, "dividedRoadSections": sections, "previewConnections": connections,
            **({'majorSidewalkPolicy':annotations['majorSidewalkPolicy']} if 'majorSidewalkPolicy' in annotations else {})}


def _xy(node: dict[str, Any]) -> tuple[float, float]:
    # The preview SVG uses south-positive Y; engine v2 uses north-positive Y.
    return float(node["x"]), -float(node["y"])


def _point(node: dict[str, Any]) -> list[float]:
    return list(_xy(node))


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _apply_crossing_annotations(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split explicitly selected sidewalks; never infer crossings by proximity.

    A marked branch joins its near sidewalk with a walk-only turn. A separate
    charged crossing reaches the far sidewalk. Image annotations are not a
    field survey, and both new links retain that distinction.
    """
    by_id = {edge["id"]: edge for edge in edges}
    splits: dict[str, list[tuple[float, str]]] = defaultdict(list)
    links: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    annotation_ids: set[str] = set()

    def anchor(edge_id: str, point: list[float], new_id: str) -> str:
        edge = by_id.get(edge_id)
        if edge is None or edge["kind"] != "sidewalk":
            raise ValueError(f"marked crossing needs a known sidewalk edge: {edge_id}")
        # The preview converter produces straight, two-point edge segments.
        a, b = edge["pathMeters"]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 0.05:
            raise ValueError(f"marked sidewalk is degenerate: {edge_id}")
        t = max(0.0, min(1.0, ((point[0] - a[0]) * dx +
                               (point[1] - a[1]) * dy) / length ** 2))
        projected = [round(a[0] + t * dx, 3), round(a[1] + t * dy, 3)]
        if _distance(point, projected) > 50.0:
            raise ValueError(f"marked crossing is too far from sidewalk: {edge_id}")
        if t * length < 0.05:
            return edge["from"]
        if (1.0 - t) * length < 0.05:
            return edge["to"]
        for old_t, old_id in splits[edge_id]:
            if abs(old_t - t) * length < 0.05:
                return old_id
        if new_id in nodes:
            raise ValueError(f"duplicate marked crossing node ID: {new_id}")
        nodes[new_id] = {"id": new_id, "xMeters": projected[0],
                         "yMeters": projected[1]}
        splits[edge_id].append((t, new_id))
        return new_id

    def location(node_id: str) -> list[float]:
        node = nodes[node_id]
        return [node["xMeters"], node["yMeters"]]

    for annotation in annotations:
        annotation_id = annotation.get("id")
        if (not isinstance(annotation_id, str) or not annotation_id or
                annotation_id in annotation_ids):
            raise ValueError("marked crossing needs a unique, non-empty ID")
        annotation_ids.add(annotation_id)
        if annotation.get("verificationStatus") != "user_marked_unverified":
            raise ValueError("image-marked crossings must remain unverified")
        if not isinstance(annotation.get("source"), str) or not annotation["source"]:
            raise ValueError("marked crossing needs annotation source information")
        wait = annotation.get("waitSeconds", 20.0)
        if (isinstance(wait, bool) or not isinstance(wait, (int, float)) or
                not math.isfinite(wait) or wait < 0):
            raise ValueError("marked crossing waitSeconds must be non-negative")
        branch = annotation.get("fromNodeId")
        if branch not in nodes:
            raise ValueError(f"unknown marked branch node: {branch}")
        if annotation.get("nearEdgeId") == annotation.get("farEdgeId"):
            raise ValueError("marked crossing needs different near/far sidewalks")
        point = location(branch)
        near = anchor(annotation["nearEdgeId"], point,
                      f"manual:{annotation_id}:near")
        far = anchor(annotation["farEdgeId"], point,
                     f"manual:{annotation_id}:far")
        turn_id = f"manual-turn:{annotation_id}"
        crossing_id = f"manual-crossing:{annotation_id}"
        if turn_id in by_id or crossing_id in by_id:
            raise ValueError("marked crossing link ID already exists")
        metadata = {"annotationId": annotation_id,
                    "verificationStatus": annotation["verificationStatus"],
                    "annotationSource": annotation["source"]}
        links.extend((
            {"id": turn_id, "from": branch, "to": near, "kind": "turn",
             "pathMeters": [point, location(near)], **metadata},
            {"id": crossing_id, "from": near, "to": far, "kind": "crossing",
             "pathMeters": [location(near), location(far)],
             "waitSeconds": float(wait), **metadata},
        ))
        records.append({**annotation, "nearNodeId": near, "farNodeId": far,
                        "turnEdgeId": turn_id, "crossingEdgeId": crossing_id})

    result = []
    for edge in edges:
        if edge["id"] not in splits:
            result.append(edge)
            continue
        ordered = [edge["from"], *(node_id for _, node_id in sorted(splits[edge["id"]])),
                   edge["to"]]
        for index, (first, second) in enumerate(zip(ordered, ordered[1:])):
            result.append({**edge, "id": f"{edge['id']}:manual:{index}",
                           "from": first, "to": second,
                           "pathMeters": [location(first), location(second)],
                           "originalEdgeId": edge["id"]})
    return result + links, records


def convert_preview_graph(
    graph: dict[str, Any], origin_wgs84: list[float],
    crossing_annotations: list[dict[str, Any]] | None = None,
    junction_annotations: list[dict[str, Any]] | None = None,
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
        tags = edge.get("sourceTags", {})
        foot, access = tags.get("foot"), tags.get("access")
        explicit_foot = foot in ("yes", "designated", "permissive")
        if (foot in ("no", "private", "use_sidepath") or
                (access in ("no", "private") and not explicit_foot) or
                (tags.get("highway") == "cycleway" and not explicit_foot)):
            raise ValueError(f"source graph contains a forbidden pedestrian edge: {edge['id']}")

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
                "sourceMajorEdgeId": edge['id'],
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
        source_crossing = edge.get('sourceTags', {}).get('footway') == 'crossing'
        edges.append({
            "id": edge["id"], "from": from_id, "to": to_id,
            "kind": "crossing" if source_crossing else "shared_way", "streetBlockId": f"shared:{edge['sourceWayId']}",
            **({"sharedWayType": "shared_alley", "widthMeters": SHARED_WIDTH_METERS[edge["kind"]]}
               if not source_crossing else {}),
            "pathMeters": [location(from_id), location(to_id)],
            **({'sourceCrossing': True, 'sourceWayId': edge['sourceWayId'],
                'sourceTags': edge['sourceTags'],
                'verificationStatus': 'online_source_unverified_on_site'} if source_crossing else {}),
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

    edges, source_crossing_records = _coalesce_source_crossings(edges)

    manual_records: list[dict[str, Any]] = []
    if crossing_annotations:
        edges, manual_records = _apply_crossing_annotations(nodes, edges, crossing_annotations)
        for record in manual_records:
            synthetic_link_ids.extend((record["turnEdgeId"], record["crossingEdgeId"]))
            synthetic_crossing_ids.append(record["crossingEdgeId"])

    junction_records: list[dict[str, Any]] = []
    if junction_annotations:
        explicit_junctions = [
            annotation for annotation in junction_annotations
            if "ports" in annotation or "connectors" in annotation
        ]
        inferred_junctions = [
            annotation for annotation in junction_annotations
            if annotation not in explicit_junctions
        ]
        validate_inferred_junction_annotations(inferred_junctions, source_edges)
        explicit_junctions.extend(expand_reviewed_junction(annotation, nodes, edges)
                                  for annotation in inferred_junctions)
        edges, junction_records = apply_junction_annotations(nodes, edges, explicit_junctions)
        for record in junction_records:
            for connector in record["connectors"]:
                synthetic_link_ids.append(connector["edgeId"])
                if connector["kind"] == "crossing":
                    synthetic_crossing_ids.append(connector["edgeId"])
        remaining_ids = {edge["id"] for edge in edges}
        synthetic_link_ids = [edge_id for edge_id in synthetic_link_ids if edge_id in remaining_ids]
        synthetic_crossing_ids = [edge_id for edge_id in synthetic_crossing_ids
                                  if edge_id in remaining_ids]

    # The shared source contains explicitly bounded local pilot sections.
    # Preserve all outside geometry and marked junctions; remove only the
    # selected inward-facing sidewalk spans, never a proximity-based guess.
    if graph.get('majorSidewalkPolicy'):
        edges,junction_records,major_report=apply_major_sidewalk_policy(graph,nodes,edges,junction_records)
        divided_records=[]
        for record in junction_records:
            for connector in record['connectors']:
                synthetic_link_ids.append(connector['edgeId'])
                if connector['kind']=='crossing':synthetic_crossing_ids.append(connector['edgeId'])
        aliases=major_report['nodeAliases']
        synthetic_link_ids.extend(major_report['convertedRoadInteriorConnectionIds'])
        synthetic_crossing_ids.extend(major_report['convertedRoadInteriorConnectionIds'])
        for record in manual_records:
            for field,edge_field in (('nearNodeId','nearEdgeId'),('farNodeId','farEdgeId')):
                if record[field] in aliases:
                    record['original'+field[0].upper()+field[1:]]=record[field]
                    record[field]=aliases[record[field]]
                record['original'+edge_field[0].upper()+edge_field[1:]]=record[edge_field]
                record[edge_field]=next(e['id'] for e in edges if e['kind']=='sidewalk' and record[field] in (e['from'],e['to']))
    else:
        edges, junction_records, divided_records = apply_divided_road_sections(
            graph, nodes, edges, junction_records)
        major_report=None
    edges, connection_records = apply_preview_connections(nodes, edges, graph.get("previewConnections", []))
    synthetic_link_ids.extend(record["connectorEdgeId"] for record in connection_records)
    remaining_ids = {edge['id'] for edge in edges}
    synthetic_link_ids = [edge_id for edge_id in synthetic_link_ids if edge_id in remaining_ids]
    synthetic_crossing_ids = [edge_id for edge_id in synthetic_crossing_ids
                              if edge_id in remaining_ids]

    # Discard any offset nodes that could not be referenced by an edge.
    used = {node_id for edge in edges for node_id in (edge["from"], edge["to"])}
    nodes = {node_id: node for node_id, node in nodes.items() if node_id in used}
    if len({edge["id"] for edge in edges}) != len(edges):
        raise ValueError("converted network contains duplicate edge IDs")
    for edge in edges:
        if edge["from"] == edge["to"] or sum(_distance(a,b) for a,b in
                zip(edge['pathMeters'],edge['pathMeters'][1:])) < 0.05:
            raise ValueError(f"converted network contains a degenerate edge: {edge['id']}")

    selection = [[round(x, 3), round(-y, 3)] for x, y in graph["selectionBoundary"][0]]
    xs, ys = zip(*selection)
    return normalize_graph({
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
            "manualCrossingAnnotations": manual_records,
            "manualJunctionAnnotations": junction_records,
            "dividedRoadSections": divided_records,
            "manualPreviewConnections": connection_records,
            "sourceCrossings": source_crossing_records,
            **({'majorSidewalkSimplification':major_report} if major_report else {}),
            **({"sourceTopology": graph["sourceTopology"]} if "sourceTopology" in graph else {}),
        },
    })
