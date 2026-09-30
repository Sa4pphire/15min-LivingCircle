"""Before/after roads-and-nodes SVG for the explicitly bounded local pilot."""

from html import escape
import json
from pathlib import Path
import sys
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.synthetic_converter import convert_preview_graph  # noqa: E402


def render(output: Path | None = None) -> Path:
    raw = json.loads((ROOT / 'frontend/src/data/demoRoadGraph.local.json').read_text('utf-8'))
    annotations = json.loads((ROOT / 'data/networks/synthetic-preview.annotations.json').read_text('utf-8'))
    after = json.loads((ROOT / 'data/networks/synthetic-preview.json').read_text('utf-8'))
    if after != convert_preview_graph(raw, [121.505, 31.333], annotations['crossings'], annotations['junctions']):
        raise ValueError('walking network is stale')
    before = convert_preview_graph({**raw, 'dividedRoadSections': []}, [121.505, 31.333],
                                  annotations['crossings'], annotations['junctions'])
    section = raw['dividedRoadSections'][0]
    selected = {f"{c['edgeId']}:{side}" for c in section['carriageways'] for side in ('left', 'right')}
    outer = {f"{c['edgeId']}:{c['outerSide']}" for c in section['carriageways']}
    box = section['boundsMeters']
    centre = (box['minX']+box['maxX'])/2
    y_min, y_max = box['minY']-50, box['maxY']+20
    scale = 440/(y_max-y_min)
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="700" viewBox="0 0 1120 700" role="img" aria-labelledby="title description">',
        '<title id="title">双车道局部试点：内侧人行道与路口连接同步关闭</title>',
        '<desc id="description">仅优化殷行路南侧双车道局部样例，包含北端路口内侧端口。保留外侧人行道、同角转弯和横穿主路的一次等待；中线过街连接仅在所标注的南侧内端口关闭。</desc>',
        '<rect width="1120" height="700" fill="#f8fbf9"/>',
        '<style>text{font-family:"Microsoft YaHei",sans-serif;fill:#173c3a} .label{font-size:14px;fill:#63877a} .edge{fill:none;stroke-linecap:round} .node{stroke:#fbfdfc;stroke-width:1}</style>',
        '<text x="40" y="45" font-size="27" font-weight="700">双车道，不等于四条人行道</text>',
        '<text class="label" x="40" y="73">殷行路南侧 · 城投茂庭附近 · 局部试点延伸至北端路口 · 合成路网，未实地核实</text>',
    ]
    for panel, (graph, heading) in enumerate(((before, '修改前：4条 sidewalk'), (after, '修改后：2条外侧 sidewalk'))):
        offset = 40+panel*550
        lines.append(f'<text x="{offset}" y="112" font-size="21" font-weight="700">{heading}</text>')
        lines.append(f'<defs><clipPath id="clip-{panel}"><rect x="{offset}" y="138" width="510" height="440"/></clipPath></defs>')
        def xy(p): return offset+255+(p[0]-centre)*scale, 138+(-p[1]-y_min)*scale
        top = 138+(box['minY']-y_min)*scale
        height = (box['maxY']-box['minY'])*scale
        lines.append(f'<rect x="{offset+75}" y="{top:.3f}" width="360" height="{height:.3f}" rx="6" fill="#e6f2e9"/>')
        lines.append(f'<text class="label" x="{offset+8}" y="{top+20:.3f}">试点区间</text>')
        lines.append(f'<g clip-path="url(#clip-{panel})">')
        ns = {n['id']: n for n in raw['nodes']}
        for c in section['carriageways']:
            edge = next(e for e in raw['edges'] if e['id'] == c['edgeId'])
            points = [xy([ns[n]['x'], -ns[n]['y']]) for n in (edge['from'], edge['to'])]
            d = f'M{points[0][0]:.3f} {points[0][1]:.3f} L{points[1][0]:.3f} {points[1][1]:.3f}'
            lines.append(f'<path d="{d}" fill="none" stroke="#d7dfda" stroke-width="{5*scale:.3f}"/>')
        for edge in graph['edges']:
            source_id = edge.get('sourceSidewalkEdgeId', edge['id'])
            if source_id not in selected:
                continue
            coords = [xy(p) for p in edge['pathMeters']]
            d = ' '.join(f'{"M" if i==0 else "L"}{p[0]:.3f} {p[1]:.3f}' for i, p in enumerate(coords))
            colour = '#147d72' if source_id in outer else ('#d1764e' if panel == 0 else '#a2b5ac')
            lines.append(f'<path class="edge" d="{d}" stroke="{colour}" stroke-width="3"><title>{escape(edge["id"])}</title></path>')
            for point in coords:
                lines.append(f'<circle class="node" cx="{point[0]:.3f}" cy="{point[1]:.3f}" r="3.5" fill="{colour}"/>')
        record = next(r for r in graph['sourceGraph']['manualJunctionAnnotations']
                      if r['id'] == 'review-yinxing-guoquan')
        by_id = {edge['id']: edge for edge in graph['edges']}
        for connector in record['connectors']:
            edge = by_id[connector['edgeId']]
            points = [xy(p) for p in edge['pathMeters']]
            d = f'M{points[0][0]:.3f} {points[0][1]:.3f} L{points[1][0]:.3f} {points[1][1]:.3f}'
            colour = '#a45d31' if edge['kind'] == 'crossing' else '#37786e'
            dash = ' stroke-dasharray="6 3"' if edge['kind'] == 'crossing' else ''
            lines.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2.5" '
                         f'{dash}><title>{escape(edge["id"])}</title></path>')
        graph_nodes = {node['id']: node for node in graph['nodes']}
        for port in record['ports']:
            node = graph_nodes[port['nodeId']]
            px, py = xy([node['xMeters'], node['yMeters']])
            if not (138 <= py <= 578):
                continue
            lines.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="3.5" fill="#fbfdfc" '
                         f'stroke="#173c3a" stroke-width="1.5"><title>{escape(port["id"])}</title></circle>')
        lines.append('</g>')
        lines.append(f'<text class="label" x="{offset+30}" y="608">{"两条车道各扩两侧，南端口仍接入路口" if panel==0 else "内侧端口关闭；外侧转弯及合法过街保留"}</text>')
    lines.extend([
        '<path d="M40 643H66" stroke="#147d72" stroke-width="3"/><text class="label" x="75" y="648">保留的外侧人行道</text>',
        '<path d="M290 643H316" stroke="#d1764e" stroke-width="3"/><text class="label" x="325" y="648">本次去除的中央纵向人行道</text>',
        '<text class="label" x="40" y="681">实线为人行道／转弯，虚线为收费过街；仅展示指定车道及其北端路口连接。其他区域不推广修改。</text>',
        '</svg>',
    ])
    output = output or ROOT/'data/networks/divided-road-pilot.svg'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    ElementTree.parse(output)
    return output


if __name__ == '__main__':
    print(render())
