"""Canonical walking-edge types without changing geometry or topology."""

LEGACY_MODES = {"sidewalk": "separated", "shared_way": "shared"}


def is_walkway(edge: dict) -> bool:
    return edge.get("kind") in ("walkway", "sidewalk", "shared_way")


def access_mode(edge: dict) -> str | None:
    kind = edge.get("kind")
    if kind in LEGACY_MODES:
        mode = LEGACY_MODES[kind]
        if "accessMode" in edge and edge["accessMode"] != mode:
            raise ValueError("legacy walking kind conflicts with accessMode")
        return mode
    if kind == "walkway":
        mode = edge.get("accessMode")
        if mode not in ("separated", "shared"):
            raise ValueError("walkway requires accessMode: separated or shared")
        return mode
    if "accessMode" in edge:
        raise ValueError("accessMode is only valid on walkway")
    return None


def is_separated_walkway(edge: dict) -> bool:
    return is_walkway(edge) and access_mode(edge) == "separated"


def is_shared_walkway(edge: dict) -> bool:
    return is_walkway(edge) and access_mode(edge) == "shared"


def normalize_edge(edge: dict) -> dict:
    """Only kind/accessMode change; IDs, endpoints and all paths are retained."""
    mode = access_mode(edge)
    return {**edge, "kind": "walkway", "accessMode": mode} if mode else dict(edge)


def normalize_graph(graph: dict) -> dict:
    return {**graph, "edges": [normalize_edge(edge) for edge in graph["edges"]]}


def legacy_graph_view(graph: dict) -> dict:
    """Adapt historical generation/audit rules, not a second stored network.

    The established corner/exterior algorithms still use their semantic labels
    internally. Public graph files and API outputs are canonical walkway.
    """
    edges = []
    for edge in graph["edges"]:
        mode = access_mode(edge)
        if mode:
            edge = {**edge, "kind": "sidewalk" if mode == "separated" else "shared_way"}
            edge.pop("accessMode", None)
        edges.append(edge)
    return {**graph, "edges": edges}
