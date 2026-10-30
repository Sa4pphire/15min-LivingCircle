"""Explicit local modelling questions and review decisions, separate from routing.

Geometry produces alternatives, never evidence of legal access. Reviews remain
unverified and are stored on graph records so the existing editor patch replays them.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math

from .junction_detection import detect_junctions
from .junction_generation import _prepare_source, REASONS


def _identity(kind, *parts):
    return f'refine-{kind}-' + hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:16]


def _point(node):
    return [node['xMeters'], node['yMeters']]


def build_refinement_inputs(graph: dict, source: dict | None = None,
                            annotations: dict | None = None, pois: list | None = None) -> list[dict]:
    """Build actionable crossings plus explicit unresolved junction/entrance inputs."""
    nodes = {n['id']: n for n in graph['nodes']}
    edges = {e['id']: e for e in graph['edges']}
    reviews = {}
    for node in nodes.values():
        for review in node.get('refinementReviews', []):
            reviews[review['inputId']] = review
    for edge in edges.values():
        if edge.get('refinementReview'):
            review = edge['refinementReview']
            reviews[review['inputId']] = review

    def item(identity, kind, label, point, node_ids, edge_ids, source_name, status, resolvable, **extra):
        return {'id': identity, 'kind': kind, 'label': label, 'centerMeters': point,
                'nodeIds': node_ids, 'edgeIds': edge_ids,
                'evidence': {'status': status, 'source': source_name},
                'review': reviews.get(identity), 'resolvable': resolvable,
                'alternatives': [], **extra}

    # Two generated ports on the same physical source arm define only a
    # crossing hypothesis. No distance-based pair finding or free-side snapping.
    groups = defaultdict(set)
    for edge in edges.values():
        if edge['kind'] != 'walkway' or edge.get('accessMode') != 'separated' or not edge.get('sourceMajorEdgeId'):
            continue
        for endpoint in (edge['from'], edge['to']):
            generation = nodes[endpoint].get('generation', {})
            candidate = generation.get('candidateId')
            if candidate:
                groups[(candidate, edge['sourceMajorEdgeId'])].add(endpoint)
    existing_crossings = defaultdict(list)
    for edge in edges.values():
        if edge['kind'] == 'crossing':
            existing_crossings[frozenset((edge['from'], edge['to']))].append(edge)
    inputs = []
    for (candidate, raw_edge), ports in sorted(groups.items()):
        if len(ports) != 2:
            continue
        ids = sorted(ports)
        identity = _identity('crossing', candidate, raw_edge)
        crossings = existing_crossings[frozenset(ids)]
        # A separately authored crossing already supplies the connection.
        if any(e.get('refinementReview', {}).get('inputId') != identity for e in crossings):
            continue
        a, b = [_point(nodes[nid]) for nid in ids]
        if math.dist(a, b) < .05:
            continue
        inputs.append(item(identity, 'missing_crossing', '生成路口的过街待确认',
            [(a[i]+b[i])/2 for i in (0, 1)], ids, [e['id'] for e in crossings],
            'ordinary-junction-v1', 'geometry_inferred_unverified', True,
            sourceEdgeIds=[raw_edge], candidateId=candidate, pathMeters=[a, b],
            currentWaitSeconds=crossings[0].get('waitSeconds', 20) if crossings else 20,
            alternatives=[{'id': 'closed', 'label': '此处不设过街'}, {'id': 'open', 'label': '允许过街并设置等待'}],
            requiredEvidence='确认横道位置、道路侧别与通行条件，记录地图来源或现场观察'))

    for edge in edges.values():
        if edge['kind'] != 'crossing' or edge.get('refinementReview', {}).get('decision') in ('open', 'closed'):
            continue
        identity = _identity('wait', edge['id'])
        path = edge['pathMeters']
        inputs.append(item(identity, 'crossing_wait', '过街等待参数待核查',
            [(path[0][i]+path[-1][i])/2 for i in (0, 1)], [edge['from'], edge['to']], [edge['id']],
            edge.get('annotationSource', 'current-walking-graph'),
            edge.get('verificationStatus', 'model_parameter_unverified'), True,
            currentWaitSeconds=edge.get('waitSeconds', 20), pathMeters=path,
            alternatives=[{'id': 'set_wait', 'label': '记录核查后的等待时间'}],
            requiredEvidence='记录等待参数的来源和适用时段；模型默认值不等于实测'))

    if source and source.get('coordinateSystem') == 'preview-local-v1':
        annotations = annotations or {}
        prepared = _prepare_source(source, annotations)
        protected = {nid for record in annotations.get('junctions', []) for nid in record.get('rawNodeIds', [])}
        for node in graph['nodes']:
            protected.update(node.get('generation', {}).get('rawNodeIds', []))
        for candidate in detect_junctions(prepared, protected_node_ids=protected):
            if candidate['status'] != 'pending' or candidate['reason'] == 'independent_walking_network':
                continue
            identity = _identity('junction', candidate['id'])
            inputs.append(item(identity, 'junction', '复杂或来源不足的路口', candidate['centerMeters'], [], [],
                'source-topology', 'source_review_required', False,
                sourceNodeId=candidate['sourceNodeId'], rawNodeIds=candidate['rawNodeIds'],
                reasonText=REASONS.get(candidate['reason'], candidate['reason']),
                requiredEvidence='补充明确道路引用、层级或专用局部连接方案后再评估',
                alternatives=[{'id': 'retain', 'label': '保留当前连接，待补充方案'}]))
    # Inventory coordinates locate an entrance question; they do not establish
    # a legal access path and cannot be evaluated through nearest-edge snapping.
    facilities = graph.get('facilities', [])
    inventory = pois if pois is not None else facilities
    bound_ids = {f.get('poiUid', f.get('id')) for f in facilities if f.get('accessEdgeId') or f.get('entrances')}
    for poi in inventory:
        uid = poi.get('uid', poi.get('id'))
        point = poi.get('localPointMeters')
        if uid is None or uid in bound_ids or poi.get('accessEdgeId') or poi.get('entrances') or not point:
            continue
        inputs.append(item(_identity('entrance', uid), 'entrance', f"设施入口待补充：{poi.get('name', uid)}",
            point, [], [], poi.get('source', 'region-poi-inventory'), 'entrance_unknown', False,
            poiId=uid, requiredEvidence='需要真实入口位置及可通行接入路径；设施坐标不能直接作为入口',
            alternatives=[{'id': 'retain', 'label': '补充入口资料后建模'}]))
    for entry in inputs:
        review = entry['review']
        if not review:
            continue
        if entry['kind'] == 'crossing_wait':
            current = edges[entry['edgeIds'][0]]
            valid = (current.get('waitSeconds', 20) == review.get('waitSeconds') and
                     current['pathMeters'] == review.get('pathMeters'))
        elif entry['kind'] == 'missing_crossing':
            current = edges.get(f"refinement-crossing:{entry['id']}")
            if review['decision'] == 'closed':
                valid = current is None and entry['pathMeters'] == review.get('pathMeters')
            else:
                valid = (current is not None and current['kind'] == 'crossing'
                         and set((current['from'], current['to'])) == set(entry['nodeIds'])
                         and current.get('waitSeconds', 20) == review.get('waitSeconds')
                         and current['pathMeters'] == review.get('pathMeters'))
            if current is not None and set((current['from'], current['to'])) != set(entry['nodeIds']):
                entry.update(resolvable=False, reasonText='已审阅连接的端点被修改，请先手动核对')
            if current is not None and current['kind'] != 'crossing':
                entry.update(resolvable=False, reasonText='原过街已改为其它路段类型，请先手动核对')
        else:
            valid = True
        if not valid:
            entry.update(previousReview=review, review=None,
                         reviewInvalidatedReason='当前线形、连接或等待已变化，旧审阅仅保留为历史')
    return sorted(inputs, key=lambda x: x['id'])


def apply_refinement_decision(graph: dict, inputs: list[dict], input_id: str, decision: str,
                              wait_seconds: float, evidence_note: str) -> dict:
    """Apply one explicit review to a copy; source and verification remain visible."""
    selected = next((x for x in inputs if x['id'] == input_id), None)
    if selected is None or not selected['resolvable']:
        raise ValueError('精化候选已变化或尚无可应用的局部方案，请重新生成计划')
    if decision not in {a['id'] for a in selected['alternatives']}:
        raise ValueError('此候选不支持该选择')
    note = evidence_note.strip()
    if len(note) < 5 or len(note) > 1000:
        raise ValueError('请填写5至1000字的核查依据')
    if isinstance(wait_seconds, bool) or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 300:
        raise ValueError('过街等待需在0至300秒之间')
    draft = deepcopy(graph)
    nodes = {n['id']: n for n in draft['nodes']}
    review = {'inputId': input_id, 'decision': decision, 'evidenceNote': note,
              'reviewedAt': datetime.now(timezone.utc).isoformat(),
              'verificationStatus': 'user_reviewed_unverified', 'waitSeconds': wait_seconds}
    if selected['kind'] == 'crossing_wait':
        for edge in draft['edges']:
            if edge['id'] == selected['edgeIds'][0]:
                review['pathMeters'] = deepcopy(edge['pathMeters'])
                edge.update(waitSeconds=wait_seconds, refinementReview=review)
    else:
        start, end = selected['nodeIds']
        generated_id = f'refinement-crossing:{input_id}'
        previous = next((e for e in draft['edges'] if e['id'] == generated_id), None)
        for edge in draft['edges']:
            if edge['id'] == generated_id and edge.get('refinementReview', {}).get('inputId') != input_id:
                raise ValueError('目标连接标识与现有路段冲突，请重新核查')
        if previous and set((previous['from'], previous['to'])) != {start, end}:
            raise ValueError('原过街连接端点已被修改，请先手动核对')
        if previous and previous['kind'] != 'crossing':
            raise ValueError('原连接类型已修改，请先手动核对')
        draft['edges'] = [e for e in draft['edges'] if e['id'] != generated_id]
        review['pathMeters'] = deepcopy(previous['pathMeters'] if previous and decision == 'open'
                                         else [_point(nodes[start]), _point(nodes[end])])
        if decision == 'open':
            draft['edges'].append({**(previous or {}), 'id': generated_id,
                'from': previous['from'] if previous else start, 'to': previous['to'] if previous else end, 'kind': 'crossing',
                'pathMeters': review['pathMeters'], 'waitSeconds': wait_seconds,
                'refinementReview': review, 'verificationStatus': 'user_reviewed_unverified',
                'sourceEdgeIds': selected['sourceEdgeIds']})
        for nid in (start, end):
            node = nodes[nid]
            node['refinementReviews'] = [r for r in node.get('refinementReviews', []) if r['inputId'] != input_id] + [review]
    return draft
