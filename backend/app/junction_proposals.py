"""Small, reviewable ordinary-junction drafts on a fresh converter output.

Source-to-walkway bindings use converter provenance and endpoint orientation,
not walking node names or spatial snapping. Crossing geometry stays opt-in.
"""

from copy import deepcopy

from .junction_annotations import apply_junction_annotations, expand_reviewed_junction
from .network_audit import audit_network
from .walking_types import legacy_graph_view, normalize_graph


def build_junction_proposal(source: dict, graph: dict, candidate: dict) -> dict:
    """Expand a ready detector candidate; never mutate the source or graph.

    Only an unedited convert_preview_graph result (or other drafts applied to
    its opposite endpoints) is supported in this pilot. Editor patch rebasing
    and evidence-backed crossing selection are deliberately outside this API.
    """
    if candidate["status"] != "ready":
        raise ValueError("only a ready ordinary junction can become a draft")
    annotation = deepcopy(candidate["proposal"])
    raw_node = candidate["rawNodeIds"][0]
    source_edges = {e["id"]: e for e in source["edges"]}
    legacy = legacy_graph_view(graph)
    bindings = {}
    for group in annotation["approachGroups"]:
        for raw_id in group["edgeIds"]:
            raw = source_edges[raw_id]
            walks = [e for e in legacy["edges"] if
                     (e.get("sourceMajorEdgeId") == raw_id if raw["kind"] == "roadMajor"
                      else e["id"] == raw_id)]
            expected = 2 if raw["kind"] == "roadMajor" else 1
            if len(walks) != expected or any(e["kind"] not in ("sidewalk", "shared_way") for e in walks):
                raise ValueError(f"approach is edited, split, or missing: {raw_id}")
            endpoint_key = "from" if raw["from"] == raw_node else "to"
            bindings[raw_id] = [{"edgeId": e["id"], "endpointNodeId": e[endpoint_key]}
                                for e in sorted(walks, key=lambda e: e["id"])]
    expanded = expand_reviewed_junction(annotation, {n["id"]: n for n in graph["nodes"]},
                                        legacy["edges"], approach_bindings=bindings)
    # Only converter-declared synthetic centre links may be retired. A real
    # footway, source crossing or manual connector must instead be reviewed.
    synthetic = set(graph.get("sourceGraph", {}).get("syntheticLinkIds", []))
    by_id = {e["id"]: e for e in legacy["edges"]}
    for edge_id in expanded["retireEdgeIds"]:
        edge = by_id[edge_id]
        if edge_id not in synthetic or edge.get("sourceCrossing") or edge.get("annotationId"):
            raise ValueError(f"draft would retire a non-synthetic connection: {edge_id}")
    # Do not claim that ordinary-road geometry establishes a legal crossing.
    for connector in expanded["connectors"]:
        if connector["kind"] == "crossing":
            connector.update({"evidence": "geometry_only", "selectedByDefault": False})
    return {"candidateId": candidate["id"], "shape": candidate["shape"],
            "sourceNodeId": candidate["sourceNodeId"], "bindings": bindings,
            "annotation": expanded, "algorithmVersion": "ordinary-junction-v1"}


def apply_junction_proposal(graph: dict, proposal: dict, *,
                            selected_crossing_ids: tuple[str, ...] = ()) -> tuple[dict, dict]:
    """Apply to a copy. Explicit crossing selection is a modelling assumption."""
    annotation = deepcopy(proposal["annotation"])
    crossing_ids = {c["id"] for c in annotation["connectors"] if c["kind"] == "crossing"}
    if not set(selected_crossing_ids) <= crossing_ids:
        raise ValueError("unknown crossing selection")
    annotation["connectors"] = [c for c in annotation["connectors"]
                                if c["kind"] != "crossing" or c["id"] in selected_crossing_ids]
    draft = legacy_graph_view(deepcopy(graph))
    nodes = {n["id"]: n for n in draft["nodes"]}
    obsolete_nodes = {p['endpointNodeId'] for p in annotation['ports']}
    retired = set(annotation.get('retireEdgeIds', []))
    for edge in draft['edges']:
        if edge['id'] in retired:
            obsolete_nodes.update((edge['from'], edge['to']))
    edges, records = apply_junction_annotations(nodes, draft["edges"], [annotation])
    record = records[0]
    provenance = {"candidateId": proposal["candidateId"], "sourceNodeId": proposal["sourceNodeId"],
                  "rawNodeIds": annotation["rawNodeIds"],
                  "sourceEdgeIds": sorted(proposal["bindings"]),
                  "algorithmVersion": proposal["algorithmVersion"],
                  "verificationStatus": "geometry_inferred_unverified"}
    changed = {p["edgeId"] for p in record["ports"]} | {c["edgeId"] for c in record["connectors"]}
    for edge in edges:
        if edge["id"] in changed:
            edge["generationHistory"] = [*edge.get("generationHistory", []), provenance]
    for port in record["ports"]:
        nodes[port["nodeId"]]["generation"] = provenance
    used = {nid for edge in edges for nid in (edge["from"], edge["to"])}
    # Only clean this junction's replaced endpoints. An editor may contain
    # unrelated, intentionally isolated nodes waiting for a later connection.
    draft.update(nodes=[n for nid, n in nodes.items() if nid in used or nid not in obsolete_nodes], edges=edges)
    draft["sourceGraph"] = {**draft.get("sourceGraph", {}), "automaticJunctionDrafts": [
        *draft.get("sourceGraph", {}).get("automaticJunctionDrafts", []), record]}
    draft = normalize_graph(draft)
    audit_network(draft)
    return draft, record
