"""Render the current C++ synthetic walking graph as a roads-and-nodes SVG.

Run from any directory:
    python backend/scripts/render_network_audit.py

The source JSON remains authoritative. This drawing does not verify whether
synthetic crossings, turns, or public access are legal in the real world.
"""

from collections import Counter
from html import escape
import json
import math
from pathlib import Path
import sys
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.walking_types import legacy_graph_view  # noqa: E402
SOURCE = ROOT / "data" / "networks" / "synthetic-preview.json"
OUTPUT = ROOT / "data" / "networks" / "synthetic-preview-audit.svg"
EDGE_ORDER = ("shared_way", "sidewalk", "turn", "crossing")
EDGE_LABELS = {
    "shared_way": "walkway · 共享接入",
    "sidewalk": "walkway · 分侧接入",
    "turn": "显式转向连接",
    "crossing": "显式过街连接（合成，未核实）",
}


def fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def project(point: list[float]) -> tuple[float, float]:
    # Engine coordinates: east-positive X, north-positive Y. SVG Y is down.
    return float(point[0]), -float(point[1])


def path_data(points: list[list[float]]) -> str:
    return " ".join(
        f"{'M' if index == 0 else 'L'}{fmt(x)} {fmt(y)}"
        for index, (x, y) in enumerate(map(project, points))
    )


def render(source: Path = SOURCE, output: Path = OUTPUT) -> dict[str, int]:
    graph = legacy_graph_view(json.loads(source.read_text(encoding="utf-8")))
    nodes = graph["nodes"]
    edges = graph["edges"]
    annotations = graph.get("sourceGraph", {}).get("manualCrossingAnnotations", [])
    manual_nodes = {record[key] for record in annotations
                    for key in ("fromNodeId", "nearNodeId", "farNodeId")}
    junctions = graph.get("sourceGraph", {}).get("manualJunctionAnnotations", [])
    manual_nodes.update(port["nodeId"] for record in junctions for port in record["ports"])
    by_id = {node["id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("duplicate node ID in walking graph")

    degree: Counter[str] = Counter()
    crossing_nodes: set[str] = set()
    grouped: dict[str, list[dict]] = {kind: [] for kind in EDGE_ORDER}
    for edge in edges:
        kind = edge["kind"]
        if kind not in grouped:
            raise ValueError(f"unexpected edge kind: {kind}")
        if edge["from"] not in by_id or edge["to"] not in by_id:
            raise ValueError(f"edge {edge['id']} has an unknown endpoint")
        if len(edge["pathMeters"]) < 2:
            raise ValueError(f"edge {edge['id']} has no drawable path")
        grouped[kind].append(edge)
        degree[edge["from"]] += 1
        degree[edge["to"]] += 1
        if kind == "crossing":
            crossing_nodes.update((edge["from"], edge["to"]))

    points = [project([node["xMeters"], node["yMeters"]]) for node in nodes]
    if not points or any(not (math.isfinite(x) and math.isfinite(y)) for x, y in points):
        raise ValueError("invalid node coordinates")
    for edge in edges:
        if any(not (math.isfinite(x) and math.isfinite(y))
               for x, y in map(project, edge["pathMeters"])):
            raise ValueError(f"edge {edge['id']} has invalid coordinates")
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    padding = 100.0
    view_x = min_x - padding
    view_y = min_y - padding
    view_width = max_x - min_x + 2 * padding
    view_height = max_y - min_y + 2 * padding
    pixel_width = 2000
    pixel_height = round(pixel_width * view_height / view_width)

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'style="display:block;max-width:100%;height:auto" '
        f'width="{pixel_width}" height="{pixel_height}" '
        f'viewBox="{fmt(view_x)} {fmt(view_y)} '
        f'{fmt(view_width)} {fmt(view_height)}" '
        'role="img" aria-labelledby="network-title network-desc">',
        '<title id="network-title">当前 C++ 合成步行路网校对图</title>',
        '<desc id="network-desc">仅展示步行道路和图节点。'
        '深绿为共享通道，蓝绿为双侧人行道，棕色为转向连接，'
        '红线与红点为未经核实的过街连接及其端点；空心橙点为度数一的断头节点。'
        '蓝色连接与蓝点为显式校对或推定的路口位置，仍未现场核实。'
        '</desc>',
        '<metadata>Source: data/networks/synthetic-preview.json; '
        'synthetic walking graph, not field-verified.</metadata>',
        '<style><![CDATA[',
        '.edge { fill: none; stroke-linecap: round; stroke-linejoin: round; }',
        '.shared_way { stroke: #245c52; stroke-width: 4.1; opacity: .85; }',
        '.sidewalk { stroke: #75a5a0; stroke-width: 3.5; opacity: .9; }',
        '.turn { stroke: #b17c44; stroke-width: 2.5; opacity: .85; }',
        '.crossing { stroke: #c64536; stroke-width: 5.5; stroke-dasharray: 7 4; }',
        '.manual-link { stroke: #009cd4; stroke-width: 6.5; opacity: 1; }',
        '.edge:hover { stroke-width: 12; opacity: 1; }',
        '.node { stroke-width: 0; }',
        '.ordinary-node { fill: #54746e; opacity: .58; }',
        '.junction-node { fill: #153c36; }',
        '.terminal-node { fill: #fff; stroke: #c3843d; stroke-width: 2.5; }',
        '.crossing-node { fill: #c64536; stroke: #fff; stroke-width: 2; }',
        '.manual-node { fill: #009cd4; stroke: #fff; stroke-width: 2; }',
        '.node:hover { fill: #141414; stroke: #fff; stroke-width: 3; }',
        ']]></style>',
    ]

    for kind in EDGE_ORDER:
        lines.append(f'<g id="roads-{kind}">')
        for edge in grouped[kind]:
            detail = [EDGE_LABELS[kind], f"ID: {edge['id']}",
                      f"from: {edge['from']}", f"to: {edge['to']}"]
            if kind == "sidewalk":
                detail.extend((f"streetBlockId: {edge['streetBlockId']}",
                               f"side: {edge['side']}"))
            elif kind == "shared_way":
                detail.extend((f"streetBlockId: {edge['streetBlockId']}",
                               f"sharedWayType: {edge['sharedWayType']}"))
            if "annotationId" in edge:
                detail.extend((f"显式标注: {edge['annotationId']}",
                               f"状态: {edge.get('verificationStatus', 'unverified')}",
                               "未现场核实"))
            if kind == "crossing":
                detail.append(f"等待时间: {edge.get('waitSeconds', 20)} 秒")
            title = escape(" | ".join(detail))
            marker_class = " manual-link" if "annotationId" in edge else ""
            lines.append(f'<path class="edge {kind}{marker_class}" d="{path_data(edge["pathMeters"])}">'
                         f'<title>{title}</title></path>')
        lines.append('</g>')

    categories = {
        "ordinary": [], "junction": [], "terminal": [], "crossing": [],
    }
    for node in nodes:
        node_id = node["id"]
        if node_id in crossing_nodes:
            category = "crossing"
        elif degree[node_id] == 1:
            category = "terminal"
        elif degree[node_id] >= 3:
            category = "junction"
        else:
            category = "ordinary"
        categories[category].append(node)
    styles = {
        "ordinary": ("ordinary-node", 1.8),
        "junction": ("junction-node", 3.8),
        "terminal": ("terminal-node", 5.2),
        "crossing": ("crossing-node", 8.0),
    }
    for category in ("ordinary", "junction", "terminal", "crossing"):
        css_class, radius = styles[category]
        lines.append(f'<g id="nodes-{category}">')
        for node in categories[category]:
            x, y = project([node["xMeters"], node["yMeters"]])
            marker_class = " manual-node" if node["id"] in manual_nodes else ""
            node_radius = 3.4 if marker_class else radius
            title = escape(
                f"节点 ID: {node['id']} | 度数: {degree[node['id']]} | "
                f"局部坐标: ({node['xMeters']}, {node['yMeters']}) m"
            )
            lines.append(
                f'<circle class="node {css_class}{marker_class}" cx="{fmt(x)}" '
                f'cy="{fmt(y)}" r="{fmt(node_radius)}">'
                f'<title>{title}</title></circle>'
            )
        lines.append('</g>')
    lines.append('</svg>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ElementTree.parse(output)
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "sidewalks": len(grouped["sidewalk"]),
        "shared_ways": len(grouped["shared_way"]),
        "turns": len(grouped["turn"]),
        "crossings": len(grouped["crossing"]),
        "crossing_nodes": len(categories["crossing"]),
        "terminal_nodes": len(categories["terminal"]),
        "junction_nodes": len(categories["junction"]),
    }


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("usage: render_network_audit.py [output.svg]")
    destination = Path(sys.argv[1]) if len(sys.argv) == 2 else OUTPUT
    counts = render(output=destination)
    print(destination.resolve())
    print(json.dumps(counts, ensure_ascii=False))
