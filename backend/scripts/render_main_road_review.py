"""Reproducible same-scale before/after panels for the current main-road batch."""

from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.synthetic_converter import convert_preview_graph, with_preview_annotations  # noqa: E402
from app.network_audit import audit_network  # noqa: E402
from app.crossroad_audit import audit_explicit_junction  # noqa: E402


def render() -> Path:
    directory = ROOT / "data/networks"
    annotations = json.loads((directory / "synthetic-preview.annotations.json").read_text("utf-8"))
    raw = json.loads((ROOT / "frontend/src/data/demoRoadGraph.local.json").read_text("utf-8"))
    raw = with_preview_annotations(raw, annotations)
    origin = json.loads((ROOT / "frontend/src/data/demoContext.extended.wgs84.json").read_text("utf-8"))["originWgs84"]
    after = convert_preview_graph(raw, origin, annotations["crossings"], annotations["junctions"])
    if after != json.loads((directory / "synthetic-preview.json").read_text("utf-8")):
        raise ValueError("walking network is stale; export before rendering")
    if any(not audit_explicit_junction(after, r)["pass"] for r in after["sourceGraph"]["manualJunctionAnnotations"]):
        raise ValueError("junction semantics failed; refusing to render an accepted model")
    before = convert_preview_graph({**raw, "dividedRoadSections": [s for s in raw["dividedRoadSections"] if not s["id"].startswith("main-")]},
                                   origin, annotations["crossings"], annotations["junctions"])
    sections = [s for s in raw["dividedRoadSections"] if s["id"].startswith("main-")]
    rows = (len(sections) + 1) // 2
    height = 170 + rows * 440
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1320" height="{height}" viewBox="0 0 1320 {height}" role="img" aria-labelledby="title desc">',
             '<title id="title">主干道外侧人行道校对：本批局部前后对比</title>',
             '<desc id="desc">同一范围、同一尺度；橙色为去除的中央纵向人行道，绿色为保留的外侧，灰色虚线为框外待核查残段。全部为合成几何，未实地核实。</desc>',
             '<rect width="100%" height="100%" fill="#fbfdfc"/>',
             '<style>text{font-family:"Microsoft YaHei",sans-serif;fill:#173c3a}.muted{fill:#668278}.edge{fill:none;stroke-linecap:round;vector-effect:non-scaling-stroke}.node{vector-effect:non-scaling-stroke}</style>',
             '<text x="28" y="42" font-size="28" font-weight="700">主干道：只保留物理外沿的人行道</text>',
             '<text x="28" y="74" font-size="16" class="muted">2026-10-01 · 6 段明确范围 · 未新增横道、闸门或地块入口 · 合成模型，未核实</text>',
             '<path d="M28 100H60" stroke="#147d72" stroke-width="3"/><text x="70" y="106" font-size="14">外侧人行道（保留）</text>',
             '<path d="M290 100H322" stroke="#d1764e" stroke-width="3"/><text x="332" y="106" font-size="14">中央纵向线（框内去除）</text>',
             '<path d="M620 100H652" stroke="#91a49b" stroke-dasharray="5 3" stroke-width="2"/><text x="662" y="106" font-size="14">框外残段（未改，待核查）</text>']
    for index, section in enumerate(sections):
        left, top = 24 + (index % 2) * 660, 146 + (index // 2) * 440
        box = section["boundsMeters"]
        x0, y0 = box["minX"] - 18, box["minY"] - 24
        width, span = box["maxX"] - box["minX"] + 36, box["maxY"] - box["minY"] + 48
        outer = {f"{c['edgeId']}:{c['outerSide']}" for c in section["carriageways"]}
        selected = {f"{c['edgeId']}:{s}" for c in section["carriageways"] for s in ("left", "right")}
        parts.append(f'<text x="{left}" y="{top}" font-size="18" font-weight="700">{index+1}. {escape(section["name"])}</text>')
        parts.append(f'<text x="{left}" y="{top+24}" font-size="11" class="muted">{escape(section["id"])} · X[{box["minX"]},{box["maxX"]}] Y[{box["minY"]},{box["maxY"]}]（向南为正）</text>')
        for panel, (graph, heading) in enumerate(((before, "修改前：4 条纵向线"), (after, "修改后：框内 2 条外侧线"))):
            px = left + panel * 320
            parts.append(f'<text x="{px}" y="{top+52}" font-size="14">{heading}</text>')
            parts.append(f'<svg x="{px}" y="{top+65}" width="302" height="304" viewBox="{x0} {y0} {width} {span}">')
            parts.append(f'<rect x="{box["minX"]}" y="{box["minY"]}" width="{box["maxX"]-box["minX"]}" height="{box["maxY"]-box["minY"]}" fill="#e7f2ec"/>')
            for edge in graph["edges"]:
                source_id = edge.get("sourceSidewalkEdgeId", edge.get("originalEdgeId", edge["id"]))
                if source_id not in selected:
                    continue
                path = edge["pathMeters"]
                d = " ".join(f'{"M" if i==0 else "L"}{p[0]} {-p[1]}' for i, p in enumerate(path))
                color = "#147d72" if source_id in outer else "#d1764e" if panel == 0 else "#91a49b"
                dash = ' stroke-dasharray="5 3"' if panel == 1 and source_id not in outer else ""
                parts.append(f'<path class="edge" d="{d}" stroke="{color}" stroke-width="2.6"{dash}><title>{escape(edge["id"])}</title></path>')
                for p in path:
                    parts.append(f'<circle class="node" cx="{p[0]}" cy="{-p[1]}" r=".7" fill="{color}"/>')
            parts.append('</svg>')
        record = next(s for s in after["sourceGraph"]["dividedRoadSections"] if s["id"] == section["id"])
        length = sum(e["removedLengthMeters"] for e in record["maskedSidewalks"])
        parts.append(f'<text x="{left}" y="{top+391}" font-size="13" class="muted">去除约 {length:.1f} 米中央伪步道；外侧道路与原有路口连接保持不变。</text>')
    parts.append('</svg>')
    output = directory / "main-road-review.svg"
    content = "\n".join(parts) + "\n"
    output.write_text(content, encoding="utf-8")
    public_output = ROOT / "frontend/public/walking-main-road-review.svg"
    public_output.parent.mkdir(parents=True, exist_ok=True)
    public_output.write_text(content, encoding="utf-8")
    report = {"schemaVersion": 1, "synthetic": True, "verificationStatus": "geometry_inferred_unverified",
              "before": audit_network(before), "after": audit_network(after),
              "appliedSectionIds": [s["id"] for s in sections],
              "newRemovedInnerLengthMeters": round(sum(e["removedLengthMeters"] for s in after["sourceGraph"]["dividedRoadSections"]
                                                       if s["id"].startswith("main-") for e in s["maskedSidewalks"]), 3),
              "junctionChecks": [audit_explicit_junction(after, r) for r in after["sourceGraph"]["manualJunctionAnnotations"]],
              "pending": annotations["mainRoadReview"]["pending"],
              "limitations": ["Additional components are explicit cut median remnants, not extra real road dead ends.",
                              "Only engine-mode data consumes these sidewalk repairs; Baidu sampled routes are not altered."]}
    (directory / "synthetic-preview.main-road-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"svg": str(output), "newRemovedInnerLengthMeters": report["newRemovedInnerLengthMeters"],
                      "beforeComponents": report["before"]["componentCount"], "afterComponents": report["after"]["componentCount"]}))
    return output


if __name__ == "__main__":
    render()
