"""Audit topology and export reproducible road-only detail panels.

Run after export_synthetic_preview.py. This reports remaining candidates;
it NEVER joins nearby roads, private paths, overpasses, or geometric crossings.
"""

from collections import Counter
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.network_audit import audit_network, find_junction_candidates  # noqa: E402
from app.synthetic_converter import convert_preview_graph, with_preview_annotations  # noqa: E402
from render_network_audit import path_data  # noqa: E402


def render_panels(graph: dict, output: Path) -> None:
    # Keep the previous eight-location atlas stable; the complete four-way
    # inventory has its own paginated sheets from audit_crossroads.py.
    records = [record for record in graph["sourceGraph"]["manualJunctionAnnotations"]
               if record["id"].startswith("review-")]
    columns = 4
    rows = (len(records) + columns - 1) // columns
    width, height = 1600, 120 + rows * 400
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             'style="display:block;max-width:100%;height:auto" '
             f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
             '<title id="title">新增路口连接校对图 · 合成、待核实</title>',
             '<desc id="desc">仅显示道路与节点。蓝色为新增同角转弯，橙色虚线为新增过街；'
             '不是已核实的人行横道地图。</desc>',
             '<rect width="100%" height="100%" fill="#fbfdfc"/>',
             '<style>text{font-family:"Microsoft YaHei",sans-serif;fill:#173c3a}'
             '.road{fill:none;stroke:#75a5a0;stroke-width:.55;stroke-linecap:round}'
             '.shared{stroke:#245c52}.turn{stroke:#009cd4;stroke-width:1.3}'
             '.cross{stroke:#d1764e;stroke-width:1.3;stroke-dasharray:2 1}'
             '.node{fill:#54746e}.port{fill:#fff;stroke:#173c3a;stroke-width:.4}</style>',
             f'<text x="30" y="42" font-size="27" font-weight="700">路口连接校对 · 本批 {len(records)} 处</text>',
             '<text x="30" y="76" font-size="17">蓝线：同角转弯（不等待）　橙色虚线：过街（默认 +20 秒）　空心点：独立路口端口</text>',
             '<text x="30" y="101" font-size="15">几何推定、未实地核实；不得据此确认合法过街、闸门开放或跨桥通行。</text>']
    for index, record in enumerate(records):
        col, row = index % columns, index // columns
        left, top = col * 400 + 20, row * 400 + 130
        turns = sum(c["kind"] == "turn" for c in record["connectors"])
        crosses = sum(c["kind"] == "crossing" for c in record["connectors"])
        label = record.get("name", record["id"])
        if "（" in label:
            name, location = label.split("（", 1)
            label_lines = [name, "（" + location]
        else:
            label_lines = [label[:19], label[19:]] if len(label) > 19 else [label]
        for line_index, line in enumerate(label_lines):
            prefix = f"{index+1:02} · " if line_index == 0 else ""
            parts.append(f'<text x="{left}" y="{top+line_index*20}" font-size="16" '
                         f'font-weight="700">{escape(prefix+line)}</text>')
        parts.append(f'<text x="{left}" y="{top+44}" font-size="14">{len(record["ports"])} 端口 / {turns} 转弯 / {crosses} 过街 · 待核实</text>')
        x, y = record["centerMeters"]
        size = 130
        x0, y0 = x - size / 2, -y - size / 2
        parts.append(f'<svg x="{left}" y="{top+55}" width="360" height="310" '
                     f'viewBox="{x0} {y0} {size} {size}">')
        visible_nodes = set()
        for edge in graph["edges"]:
            path = edge["pathMeters"]
            xs, ys = [p[0] for p in path], [-p[1] for p in path]
            if max(xs) < x0 or min(xs) > x0+size or max(ys) < y0 or min(ys) > y0+size:
                continue
            local = edge.get("annotationId") == record["id"]
            css = "road shared" if edge["kind"] == "shared_way" else "road"
            if local:
                css += " cross" if edge["kind"] == "crossing" else " turn"
            detail = f'{edge["id"]} | {edge["kind"]}'
            parts.append(f'<path class="{css}" d="{path_data(path)}"><title>{escape(detail)}</title></path>')
            visible_nodes.update((edge["from"], edge["to"]))
        port_ids = {p["nodeId"] for p in record["ports"]}
        for node in graph["nodes"]:
            px, py = node["xMeters"], -node["yMeters"]
            if node["id"] not in visible_nodes or not (x0 <= px <= x0+size and y0 <= py <= y0+size):
                continue
            port = node["id"] in port_ids
            css, radius = ("port", 1.2) if port else ("node", .45)
            parts.append(f'<circle class="{css}" cx="{px}" cy="{py}" r="{radius}">'
                         f'<title>{escape(node["id"])}</title></circle>')
        parts.append('</svg>')
    parts.append('</svg>')
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> None:
    directory = ROOT / "data/networks"
    graph = json.loads((directory / "synthetic-preview.json").read_text("utf-8"))
    annotations = json.loads((directory / "synthetic-preview.annotations.json").read_text("utf-8"))
    raw = json.loads((ROOT / "frontend/src/data/demoRoadGraph.local.json").read_text("utf-8"))
    raw = with_preview_annotations(raw, annotations)
    origin = json.loads((ROOT / "frontend/src/data/demoContext.extended.wgs84.json").read_text("utf-8"))["originWgs84"]
    legacy = [r for r in annotations["junctions"] if r["verificationStatus"] == "user_marked_unverified"]
    # The local divided-road closure depends on a reviewed junction omitted
    # from this legacy baseline.
    before = convert_preview_graph({**raw, 'dividedRoadSections': []}, origin,
                                   annotations["crossings"], legacy)
    expected = convert_preview_graph(raw, origin, annotations["crossings"], annotations["junctions"])
    if expected != graph:
        raise ValueError("generated walking network is stale; run export_synthetic_preview.py first")
    records = graph["sourceGraph"]["manualJunctionAnnotations"]
    candidates = find_junction_candidates(raw, records)
    report = {"schemaVersion": 1, "synthetic": True,
              "coordinateSystem": "engine-local-meters", "before": audit_network(before),
              "after": audit_network(graph),
              "reviewedJunctions": [{"id": r["id"], "name": r.get("name", r["id"]),
                                     "centerMeters": r["centerMeters"],
                                     "verificationStatus": r["verificationStatus"],
                                     "ports": len(r["ports"]),
                                     "connectors": dict(Counter(c["kind"] for c in r["connectors"]))}
                                    for r in records],
              "rawCandidateCount": len(candidates),
              "rawCandidatesNeedingReview": sum(not c["reviewedBy"] for c in candidates),
              "junctionCandidates": candidates,
              "limitations": ["Bottom map does not verify legal pedestrian crossings.",
                              "Raw graph lacks bridge/layer/access tags; unresolved junctions are NOT auto-joined.",
                              "Disconnected components and dead ends can be legitimate, not necessarily errors.",
                              "Quick-mode source graph remains unchanged; repairs apply to C++ expert-mode fixture."]}
    output = directory / "synthetic-preview.connectivity-report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    render_panels(graph, directory / "synthetic-preview-junction-review.svg")
    print(json.dumps({"beforeComponents": report["before"]["componentCount"],
                      "afterComponents": report["after"]["componentCount"],
                      "remainingCandidateVertices": report["rawCandidatesNeedingReview"],
                      "structuralErrors": report["after"]["structuralErrors"]}))


if __name__ == "__main__":
    main()
