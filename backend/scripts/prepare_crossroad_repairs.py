"""Dry-run selected crossroad proposals and export a reviewable annotation file.

Does not overwrite the authoritative annotations or walking network. Run the
crossroad audit first. Output must pass the independent junction rule checker.
"""

from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend'))
from app.crossroad_audit import audit_explicit_junction, discover_crossroads  # noqa: E402
from app.junction_annotations import expand_reviewed_junction  # noqa: E402
from app.network_audit import audit_network  # noqa: E402
from app.synthetic_converter import convert_preview_graph  # noqa: E402


def main() -> None:
    directory = ROOT/'data/networks'
    annotations = json.loads((directory/'synthetic-preview.annotations.json').read_text('utf-8'))
    proposals = json.loads((directory/'synthetic-preview.crossroad-proposals.json').read_text('utf-8'))
    raw = json.loads((ROOT/'frontend/src/data/demoRoadGraph.local.json').read_text('utf-8'))
    origin = json.loads((ROOT/'frontend/src/data/demoContext.extended.wgs84.json').read_text('utf-8'))['originWgs84']
    baseline = convert_preview_graph(raw, origin, annotations['crossings'])
    base_nodes = {n['id']:n for n in baseline['nodes']}
    # This particular nearby T vertex belongs to the same asymmetric four-way
    # approach. Preserve its original annotation ID and explicitly mark the
    # two median-to-shared crossings rather than silently dropping median ports.
    asymmetric = deepcopy(next(c['proposal'] for c in discover_crossroads(raw)
                               if c['id']=='crossroad-039d213fdb36'))
    asymmetric.update(id='review-south-central', name='南部斜交路口（双车道接共享通道）',
                      source='2026-09-29 十字路口专项校对：补入南侧旧 T 节点；双车道中间人行道通往相对共享入口时使用显式、带等待的过街。未核实通行许可。')
    valid = []
    for middle in combinations(range(4), 2):
        variant = deepcopy(asymmetric)
        variant['medianConnections'] = [{'id':f'median-shared-{i}', 'fromPort':f'arm-0-{i}',
                                          'toPort':'arm-2-0', 'waitSeconds':20} for i in middle]
        try:
            expand_reviewed_junction(variant, base_nodes, baseline['edges'])
        except ValueError:
            continue
        valid.append(variant)
    if len(valid) != 1:
        raise ValueError('asymmetric junction must have one unambiguous explicit median configuration')
    replacement = valid[0]
    asymmetric_changes = sum(r['id']=='review-south-central' and r != replacement
                             for r in annotations['junctions'])
    annotations['junctions'] = [replacement if r['id']=='review-south-central' else r for r in annotations['junctions']]
    existing = {r['id'] for r in annotations['junctions']}
    proposals = [p for p in proposals if p['id'] not in existing]
    annotations['junctions'].extend(proposals)
    report = json.loads((directory/'synthetic-preview.crossroads-report.json').read_text('utf-8'))
    discovered = {c['id']:c for c in discover_crossroads(raw)}
    expanded_centres = 0
    for candidate in report['crossroads']:
        if candidate['status'] != 'partial_explicit_model' or len(candidate['modelIds']) != 1:
            continue
        complete = discovered[candidate['id']]['proposal']
        if complete is None:
            continue
        old_id = candidate['modelIds'][0]
        old = next(r for r in annotations['junctions'] if r['id']==old_id)
        if 'approachGroups' not in old:
            raise ValueError('legacy explicit port model requires manual centre review')
        updated = {**old, 'rawNodeIds':complete['rawNodeIds'],
                   'centerMeters':complete['centerMeters'], 'approachGroups':complete['approachGroups']}
        annotations['junctions'] = [updated if r['id']==old_id else r for r in annotations['junctions']]
        expanded_centres += 1
    graph = convert_preview_graph(raw, origin, annotations['crossings'], annotations['junctions'])
    topology = audit_network(graph)
    checks = [audit_explicit_junction(graph, r) for r in graph['sourceGraph']['manualJunctionAnnotations']]
    failures = [r for r in checks if not r['pass']]
    if failures:
        raise ValueError(f'junction rule failures: {failures}')
    (directory/'synthetic-preview.crossroad-repairs.proposed.json').write_text(
        json.dumps(annotations, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'newJunctions':len(proposals), 'extendedExistingJunctions':asymmetric_changes+expanded_centres,
                      'ruleChecks':len(checks), 'topology':{k:topology[k] for k in ('nodes','edges','componentCount','largestComponentNodes')},
                      'newConnectors':{kind:sum(c['kind']==kind for r in graph['sourceGraph']['manualJunctionAnnotations']
                                              if r['id'] in {p['id'] for p in proposals} for c in r['connectors'])
                                       for kind in ('turn','crossing')}}))


if __name__=='__main__':
    main()
