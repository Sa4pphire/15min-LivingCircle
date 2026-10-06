"""Catalogue all four-way source nodes and render road-only review sheets.

This script exports proposals but NEVER applies them to the annotation file.
"""

import argparse
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.crossroad_audit import audit_crossroads  # noqa: E402
from app.network_audit import audit_network  # noqa: E402
from app.synthetic_converter import convert_preview_graph, with_preview_annotations  # noqa: E402
from render_network_audit import path_data  # noqa: E402


LABELS = {"explicit_model_pass": "显式路口 · 规则通过 · 待核实",
          "shared_junction_pass": "共享通道 · 不需要过街等待",
          "missing_explicit_model": "缺少显式转弯／过街模型",
          "needs_manual_review": "复杂几何 · 需人工判断",
          "partial_explicit_model": "只处理部分中心节点",
          "explicit_model_failed": "现有模型违反规则",
          "shared_junction_failed": "共享通道连接异常"}


def render_sheets(graph: dict, report: dict, directory: Path, prefix: str) -> list[str]:
    candidates = sorted(report["crossroads"], key=lambda r: (
        r["status"] == "shared_junction_pass", r["status"] == "needs_manual_review",
        -r["centerMeters"][1], r["centerMeters"][0]))
    pages = []
    for start in range(0, len(candidates), 12):
        page = start//12 + 1
        name = f"{prefix}-{page:02}.svg"
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1380" '
                 'viewBox="0 0 1600 1380" style="display:block;max-width:100%;height:auto" '
                 'role="img" aria-labelledby="title desc">',
                 f'<title id="title">十字路口专项校对 · 第 {page} 页 · 合成数据</title>',
                 '<desc id="desc">仅显示道路和节点；蓝色为转弯，橙色虚线为过街。所有推定过街仍待核实。</desc>',
                 '<rect width="100%" height="100%" fill="#fbfdfc"/>',
                 '<style>text{font-family:"Microsoft YaHei",sans-serif;fill:#173c3a}'
                 '.road{fill:none;stroke:#75a5a0;stroke-width:.65;stroke-linecap:round}'
                 '.shared{stroke:#245c52}.turn{stroke:#009cd4;stroke-width:1.3}'
                 '.cross{stroke:#d1764e;stroke-width:1.4;stroke-dasharray:2 1}'
                 '.node{fill:#54746e}.port{fill:#fff;stroke:#173c3a;stroke-width:.4}'
                 '.center{fill:none;stroke:#d1764e;stroke-width:.7;stroke-dasharray:2 2}</style>',
                 f'<text x="25" y="40" font-size="27" font-weight="700">十字路口专项校对 · 第 {page} 页</text>',
                 '<text x="25" y="74" font-size="17">蓝线：同角转弯　橙色虚线：过街（等待另计）　空心点：独立端口</text>',
                 '<text x="25" y="101" font-size="15">“规则通过”不等于现实通行已核实；不得据此确认横道、隔离带或闸门。</text>']
        for index, candidate in enumerate(candidates[start:start+12]):
            left, top = index % 4 * 400 + 20, index//4 * 415 + 140
            label = f"{start+index+1:03} · {candidate['id']}"
            parts.append(f'<text x="{left}" y="{top}" font-size="15" font-weight="700">{escape(label)}</text>')
            parts.append(f'<text x="{left}" y="{top+24}" font-size="14">{LABELS[candidate["status"]]}</text>')
            cx, cy = candidate["centerMeters"]
            parts.append(f'<text x="{left}" y="{top+46}" font-size="12">'
                         f'中心 {cx:g}, {cy:g} · {len(candidate["rawNodeIds"])} 原节点 / {candidate["approachCount"]} 方向</text>')
            size = 130
            x0, y0 = cx-size/2, -cy-size/2
            parts.append(f'<svg x="{left}" y="{top+56}" width="360" height="335" viewBox="{x0} {y0} {size} {size}">')
            visible = set()
            port_nodes = {p["nodeId"] for r in graph["sourceGraph"]["manualJunctionAnnotations"]
                          if r["id"] in candidate["modelIds"] for p in r["ports"]}
            for edge in graph["edges"]:
                path = edge["pathMeters"]
                xs, ys = [p[0] for p in path], [-p[1] for p in path]
                if max(xs)<x0 or min(xs)>x0+size or max(ys)<y0 or min(ys)>y0+size:
                    continue
                css = "road shared" if edge["kind"] == "shared_way" else "road"
                if edge.get("annotationId") in candidate["modelIds"]:
                    css += " cross" if edge["kind"] == "crossing" else " turn"
                title = escape(f'{edge["id"]} | {edge["kind"]}')
                parts.append(f'<path class="{css}" d="{path_data(path)}"><title>{title}</title></path>')
                visible.update((edge["from"], edge["to"]))
            for node in graph["nodes"]:
                x,y = node["xMeters"], -node["yMeters"]
                if node["id"] not in visible or not (x0<=x<=x0+size and y0<=y<=y0+size):
                    continue
                css, radius = ("port",1.2) if node["id"] in port_nodes else ("node",.45)
                parts.append(f'<circle class="{css}" cx="{x}" cy="{y}" r="{radius}"><title>{escape(node["id"])}</title></circle>')
            if candidate["status"] not in ("explicit_model_pass", "shared_junction_pass"):
                parts.append(f'<circle class="center" cx="{cx}" cy="{-cy}" r="15"><title>{escape(candidate.get("reason") or "待补建模")}</title></circle>')
            parts.append('</svg>')
        parts.append('</svg>')
        (directory/name).write_text('\n'.join(parts)+'\n', encoding='utf-8')
        pages.append(name)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sheets', action='store_true')
    args = parser.parse_args()
    directory = ROOT/'data/networks'
    raw = json.loads((ROOT/'frontend/src/data/demoRoadGraph.local.json').read_text('utf-8'))
    origin = json.loads((ROOT/'frontend/src/data/demoContext.extended.wgs84.json').read_text('utf-8'))['originWgs84']
    annotations = json.loads((directory/'synthetic-preview.annotations.json').read_text('utf-8'))
    raw = with_preview_annotations(raw, annotations)
    graph = json.loads((directory/'synthetic-preview.json').read_text('utf-8'))
    expected = convert_preview_graph(raw, origin, annotations['crossings'], annotations['junctions'])
    if expected != graph:
        raise ValueError('walking graph is stale; export_synthetic_preview.py first')
    topology = audit_network(graph)
    # The current local mask names a reviewed junction, so it cannot be
    # applied to the unreviewed candidate-discovery baseline.
    baseline = convert_preview_graph({**raw, 'dividedRoadSections': [], 'majorSidewalkPolicy':None}, origin,
                                     annotations['crossings'])
    report = audit_crossroads(raw, graph, baseline)
    report['beforeCurrentBatch'] = annotations.get('crossroadReviewBaseline')
    report['currentTopology'] = topology
    if args.sheets:
        report['reviewSheets'] = render_sheets(graph, report, directory, 'synthetic-preview-crossroads')
    (directory/'synthetic-preview.crossroads-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    proposals = [r['proposal'] for r in report['crossroads'] if r['status']=='missing_explicit_model']
    (directory/'synthetic-preview.crossroad-proposals.json').write_text(json.dumps(proposals, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'candidates':report['candidateCount'], 'rawFourWayVertices':report['rawFourWayVertexCount'],
                      'statuses':report['statuses'], 'ruleFailures':[r for r in report['explicitJunctionChecks'] if not r['pass']],
                      'proposals':len(proposals)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
