"""Read-only structural audit; findings never authorize automatic connections."""

from collections import Counter, defaultdict
import math
from typing import Any


def _finite_number(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and
            math.isfinite(value))


def audit_network(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = graph["edges"]
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("duplicate node ID")
    if len({edge["id"] for edge in edges}) != len(edges):
        raise ValueError("duplicate edge ID")
    for node in nodes.values():
        if (not isinstance(node["id"], str) or not node["id"] or
                not _finite_number(node.get("xMeters")) or
                not _finite_number(node.get("yMeters"))):
            raise ValueError("invalid node coordinates or ID")
    adjacency: dict[str, list[str]] = defaultdict(list)
    degree: Counter[str] = Counter()
    for edge in edges:
        if edge["kind"] not in ("sidewalk", "shared_way", "turn", "crossing"):
            raise ValueError(f"invalid edge kind: {edge['id']}")
        a, b = edge["from"], edge["to"]
        if a not in nodes or b not in nodes or a == b:
            raise ValueError(f"invalid endpoints: {edge['id']}")
        path = edge["pathMeters"]
        if (not isinstance(path, list) or len(path) < 2 or
                any(not isinstance(p, list) or len(p) != 2 or
                    not all(_finite_number(v) for v in p) for p in path)):
            raise ValueError(f"invalid geometry: {edge['id']}")
        length = sum(math.dist(p, q) for p, q in zip(path, path[1:]))
        if length < 0.05:
            raise ValueError(f"zero-length edge: {edge['id']}")
        for nid, point in ((a, path[0]), (b, path[-1])):
            expected = [nodes[nid]["xMeters"], nodes[nid]["yMeters"]]
            # Match C++ validate_graph's tolerance; a looser preview-only check
            # can accept a JSON graph that the routing engine then rejects.
            if math.dist(expected, point) > 1e-6:
                raise ValueError(f"geometry does not meet node: {edge['id']}")
        if edge["kind"] == "crossing":
            wait = edge.get("waitSeconds", 20)
            if not _finite_number(wait) or wait < 0:
                raise ValueError(f"invalid crossing wait: {edge['id']}")
        adjacency[a].append(b)
        adjacency[b].append(a)
        degree.update((a, b))
    seen: set[str] = set()
    sizes = []
    for node_id in nodes:
        if node_id in seen:
            continue
        seen.add(node_id)
        pending = [node_id]
        count = 0
        while pending:
            current = pending.pop()
            count += 1
            for other in adjacency[current]:
                if other not in seen:
                    seen.add(other)
                    pending.append(other)
        sizes.append(count)
    sizes.sort(reverse=True)
    return {"nodes": len(nodes), "edges": len(edges),
            "edgeKinds": dict(sorted(Counter(edge["kind"] for edge in edges).items())),
            "componentCount": len(sizes), "largestComponentNodes": sizes[0] if sizes else 0,
            "componentSizes": sizes, "terminalNodes": sum(d == 1 for d in degree.values()),
            "isolatedNodes": len(nodes) - len(degree), "structuralErrors": 0}


def find_junction_candidates(source: dict[str, Any], reviewed: list[dict[str, Any]]) -> list[dict]:
    """Report raw branch vertices, not geometric crossings or verified crosswalks."""
    nodes = {node["id"]: node for node in source["nodes"]}
    incidence: dict[str, list[dict]] = defaultdict(list)
    for edge in source["edges"]:
        if edge["kind"] != "inferredJunction":
            incidence[edge["from"]].append(edge)
            incidence[edge["to"]].append(edge)
    records = []
    for node_id, edges in incidence.items():
        major = sum(edge["kind"] == "roadMajor" for edge in edges)
        if major < 2 or len(edges) < 3:
            continue
        node = nodes[node_id]
        center = [node["x"], -node["y"]]
        covered = [record["id"] for record in reviewed
                   if (node_id in record["rawNodeIds"] if "rawNodeIds" in record
                       else math.dist(center, record["centerMeters"]) <= 25)]
        records.append({"rawNodeId": node_id, "centerMeters": center,
                        "majorApproaches": major, "edgeIds": [e["id"] for e in edges],
                        "reviewedBy": covered,
                        "status": "reviewed_unverified" if covered else "needs_review"})
    return sorted(records, key=lambda r: (-r["centerMeters"][1], r["centerMeters"][0]))
