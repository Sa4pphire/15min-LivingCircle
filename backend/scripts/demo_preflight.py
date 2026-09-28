"""Read-only launcher preflight; do not generate or replace network data."""

import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.local_experiment import load_local_experiment_request
from app.network import UnsupportedAreaError, _to_map_coordinate, load_engine_request
from app.schemas import CenterPoint
from app.settings import settings


def midpoint(path):
    lengths = [math.dist(a, b) for a, b in zip(path, path[1:])]
    remaining = sum(lengths) / 2
    for a, b, length in zip(path, path[1:], lengths):
        if length > 0 and remaining <= length:
            ratio = remaining / length
            return [a[i] + ratio * (b[i] - a[i]) for i in range(2)]
        remaining -= length
    raise ValueError("Walking edge has zero length")


def example_request(path, *, local):
    graph = json.loads(path.read_text(encoding="utf-8-sig"))
    synthetic = graph.get("synthetic") is True
    coord_type = "wgs84ll" if synthetic and "originWgs84" in graph else "bd09ll"
    origin = graph["originWgs84" if coord_type == "wgs84ll" else "originBd09"]
    loader = load_local_experiment_request if local else load_engine_request
    preferred = graph.get("originEdgeId", "w:154811345:2:0")
    edges = sorted(graph["edges"], key=lambda edge: edge["id"] != preferred)
    candidates = []
    seed = graph.get("demoCenterWgs84" if coord_type == "wgs84ll" else "demoCenterBd09")
    if seed:
        candidates.append((seed["lng"], seed["lat"], graph.get("originEdgeId")))
    # Use declared demo centers or actual edge positions, not invented connectors.
    for edge in edges:
        if edge.get("kind") not in ("sidewalk", "shared_way"):
            continue
        for point in (midpoint(edge["pathMeters"]), edge["pathMeters"][0], edge["pathMeters"][-1]):
            lng, lat = _to_map_coordinate(point, origin)
            candidates.append((lng, lat, edge["id"]))
    last_error = None
    for lng, lat, edge_id in candidates:
        center = CenterPoint(lng=lng, lat=lat, coordType=coord_type)
        try:
            loader(center, edge_id, network_path=path)
        except UnsupportedAreaError as exc:
            last_error = exc
            continue
        request = {"center": center.model_dump()}
        if edge_id:
            request["originEdgeId"] = edge_id
        if not local:
            request["minutes"] = 15
        return request, "synthetic" if synthetic else "manual"
    raise ValueError(f"No eligible public walking edge in the supported center area: {path}; {last_error}")


def main():
    # Imports verify the installed backend dependencies without installing anything.
    import fastapi
    import httpx
    import uvicorn

    local, local_source = example_request(settings.local_experiment_network_path, local=True)
    synthetic, _ = example_request(settings.synthetic_network_path, local=False)
    print(json.dumps({
        "localRequest": local,
        "syntheticRequest": synthetic,
        "localSource": local_source,
    }, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Demo preflight failed: {exc}", file=sys.stderr)
        sys.exit(1)
