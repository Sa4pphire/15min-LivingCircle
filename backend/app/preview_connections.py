"""Explicit unverified shared-path repairs, never nearest-neighbour discovery."""

import math


def apply_preview_connections(nodes: dict, edges: list[dict], annotations: list[dict]) -> tuple[list[dict], list[dict]]:
    if not isinstance(annotations, list):
        raise ValueError("preview connections must be a list")
    by_id = {edge["id"]: edge for edge in edges}
    records, seen, targets = [], set(), set()
    for item in annotations:
        name = item.get("id") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("preview connection needs a unique ID")
        seen.add(name)
        if (item.get("verificationStatus") != "user_marked_unverified" or
                not isinstance(item.get("source"), str) or not item["source"]):
            raise ValueError("preview connection needs an explicit unverified source")
        endpoint = item.get("endpointNodeId")
        target = item.get("targetEdgeId")
        edge = by_id.get(target)
        if (endpoint not in nodes or not endpoint.startswith("shared:") or
                edge is None or edge["kind"] != "shared_way" or target in targets or
                endpoint in (edge["from"], edge["to"]) or len(edge["pathMeters"]) != 2):
            raise ValueError("preview repair needs a distinct shared-path endpoint and target")
        targets.add(target)
        point = item.get("targetPointMeters")
        gap_limit = item.get("maxGapMeters")
        if (not isinstance(point, list) or len(point) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point) or
                isinstance(gap_limit, bool) or not isinstance(gap_limit, (int, float)) or
                not math.isfinite(gap_limit) or not 0 < gap_limit <= 10):
            raise ValueError("preview repair needs a finite anchor and a bounded gap")
        a, b = edge["pathMeters"]
        length = math.dist(a, b)
        if length < 0.05:
            raise ValueError("preview target is degenerate")
        t = sum((point[i] - a[i]) * (b[i] - a[i]) for i in (0, 1)) / length ** 2
        projected = [a[i] + t * (b[i] - a[i]) for i in (0, 1)]
        # Accept only the rounding error of a recorded millimetre anchor.
        if not 0 < t < 1 or min(t, 1-t) * length < 0.05 or math.dist(point, projected) > 0.002:
            raise ValueError("preview anchor must lie inside the explicitly named target")
        start = [nodes[endpoint]["xMeters"], nodes[endpoint]["yMeters"]]
        gap = math.dist(start, point)
        if not 0.05 <= gap <= gap_limit:
            raise ValueError("preview connection exceeds its annotated gap")
        anchor_id, link_id = f"synthetic:{name}:anchor", f"synthetic-link:{name}"
        split_ids = [f"{target}:manual:{name}:{i}" for i in (0, 1)]
        if anchor_id in nodes or any(edge_id in by_id for edge_id in [link_id, *split_ids]):
            raise ValueError("preview repair IDs collide with existing data")
        nodes[anchor_id] = {"id": anchor_id, "xMeters": point[0], "yMeters": point[1]}
        del by_id[target]
        for index, (first, second, path) in enumerate((
                (edge["from"], anchor_id, [a, point]), (anchor_id, edge["to"], [point, b]))):
            by_id[split_ids[index]] = {**edge, "id": split_ids[index], "from": first, "to": second,
                                       "pathMeters": path, "originalEdgeId": target}
        by_id[link_id] = {"id": link_id, "from": endpoint, "to": anchor_id, "kind": "turn",
                         "pathMeters": [start, point], "annotationId": name, "synthetic": True,
                         "verificationStatus": item["verificationStatus"], "annotationSource": item["source"]}
        records.append({**item, "anchorNodeId": anchor_id, "connectorEdgeId": link_id,
                        "gapMeters": round(gap, 3)})
    return list(by_id.values()), records
