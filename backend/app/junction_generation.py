"""Incremental ordinary-junction generation on an existing editor graph.

Keep the established converter/manual-edit base unchanged. The returned graph
is an editor draft; its node/edge provenance survives the existing patch saver.
"""

from collections import Counter, defaultdict
from copy import deepcopy

from .junction_detection import detect_junctions
from .junction_proposals import build_junction_proposal, apply_junction_proposal
from .synthetic_converter import convert_preview_graph, with_preview_annotations
from .walking_types import normalize_graph


REASONS = {
    'source_refs_missing': '缺少明确的源节点与道路引用',
    'source_topology_incomplete': '源节点引用不完整或位于裁剪边界',
    'source_node_access_review_required': '路口包含闸门等通行限制，需要核查',
    'layer_or_structure_unsupported': '桥梁、隧道或不同层级暂不自动处理',
    'pedestrian_access_restricted': '源数据限制步行通行',
    'walking_direction_unsupported': '存在步行方向限制',
    'complex_road_structure': '环岛或匝道等复杂结构暂不支持',
    'independent_walking_network': '独立步道或过街设施需保留原连接',
    'parallel_or_fork_ambiguity': '平行道路或分叉方向存在歧义',
    'ambiguous_opposite_pairs': '存在多组近似相对方向',
    'ambiguous_or_missing_opposite_pairs': '四向道路的相对方向不明确',
    'irregular_four_way': '四向夹角不适用普通模板',
    'template_angle_not_supported': '道路夹角不适用普通模板',
    'complex_degree': '接近方向超过四个',
    'divided_road_unsupported': '双幅道路需要专用模板',
    'multiple_shared_approaches': '多条共享支路需要人工确认连接',
    'approach_too_short': '接近段不足以稳定识别方向',
    'template_approach_too_short': '接近段太短，无法截退端口',
    'direction_unstable': '道路方向在观察范围内不稳定',
    'template_direction_mismatch': '源方向与模板接近段方向不一致',
    'loop_or_roundabout': '发现闭环或环岛结构',
    'existing_model_preserved': '已有路口标注，保留当前模型',
    'shared_junction_preserved': '共享步道交点保留原连接',
    'already_generated': '已自动生成，保留当前模型',
    'manual_edit_preserved': '接近路段或端点已编辑，保留当前修改',
    'existing_crossing_preserved': '当前过街连接需要保留，不自动迁移',
    'template_mapping_review': '当前路段已拆分或与普通模板不匹配',
}


def _prepare_source(source: dict, annotations: dict) -> dict:
    prepared = deepcopy(with_preview_annotations(source, annotations))
    topology = deepcopy(annotations.get('sourceTopology', source.get('sourceTopology', {})))
    prepared['sourceTopology'] = topology
    nodes = {n['id']: n for n in prepared['nodes']}
    incident = defaultdict(list)
    for edge in prepared['edges']:
        if edge['kind'] != 'inferredJunction':
            incident[edge['from']].append(edge)
            incident[edge['to']].append(edge)
    ways = {str(w.get('id', w.get('sourceWayId'))): w for w in topology.get('ways', [])}
    anchors = defaultdict(list)
    for record in topology.get('junctions', []):
        point = record.get('pointMeters')
        if isinstance(point, list) and len(point) == 2:
            anchors[f'p:{point[0]:.1f}:{point[1]:.1f}'].append(record)
    for node_id, records in anchors.items():
        if node_id not in nodes or len(records) != 1:
            continue
        record, node = records[0], nodes[node_id]
        sid = record.get('sourceNodeId')
        arms = incident[node_id]
        if sid is None or not arms or (node.get('sourceNodeId') is not None and str(node['sourceNodeId']) != str(sid)):
            continue
        if {str(e.get('sourceWayId')) for e in arms} != {str(w) for w in record.get('wayIds', [])}:
            continue
        if not all(str(sid) in {str(n) for n in ways.get(str(e.get('sourceWayId')), {}).get('nodeIds', [])}
                   for e in arms):
            continue
        node['sourceNodeId'] = sid
        record['nodeId'] = node_id
        node['sourceIdentityMapping'] = 'unique_preview_anchor_and_shared_way_refs'
    return prepared


def _walk_index(graph: dict, source: dict) -> dict:
    raw_ids = {e['id'] for e in source['edges']}
    result = defaultdict(list)
    for edge in graph['edges']:
        raw = edge.get('sourceMajorEdgeId')
        if raw in raw_ids:
            result[raw].append(edge)
        elif edge['id'] in raw_ids:
            result[edge['id']].append(edge)
    return result


def _protection(source: dict, annotations: dict, graph: dict, reference: dict) -> tuple[set, set]:
    """Protect both modern raw-node annotations and earlier explicit ports."""
    protected, generated = set(), set()
    raw = {e['id']: e for e in source['edges']}
    walks = {e['id']: e for e in reference['edges']}
    records = [*annotations.get('junctions', []),
               *reference.get('sourceGraph', {}).get('manualJunctionAnnotations', [])]
    for record in records:
        protected.update(record.get('rawNodeIds', []))
        for port in record.get('ports', []):
            edge = walks.get(port.get('edgeId'))
            if edge is None:
                continue
            source_edge = raw.get(edge.get('sourceMajorEdgeId', edge['id']))
            if source_edge is None:
                continue
            # Converted records identify the generated node at the original
            # selected endpoint, retaining orientation through conversion.
            node_id = port.get('nodeId')
            if node_id == edge['from']:
                protected.add(source_edge['from'])
            elif node_id == edge['to']:
                protected.add(source_edge['to'])
            elif not record.get('rawNodeIds'):
                # A historical explicit-port record may have had its node
                # migrated by exterior-sidewalk processing. Preserve both
                # ends of those few selected source arms instead of guessing.
                protected.update((source_edge['from'], source_edge['to']))
    for node in graph['nodes']:
        generated.update(node.get('generation', {}).get('rawNodeIds', []))
    for edge in graph['edges']:
        for record in edge.get('generationHistory', []):
            generated.update(record.get('rawNodeIds', []))
    return protected, generated


def _changed_approach(candidate: dict, source: dict, current: dict,
                      baseline_walks: dict, baseline_nodes: dict, baseline_edges: dict) -> bool:
    """Detect saved/unsaved manual changes without treating all patches as manual.

    An opposite endpoint changed by this generator may share the same edge.
    The endpoint being generated must still match its converter baseline.
    """
    raw_node = candidate['rawNodeIds'][0]
    raw_edges = {e['id']: e for e in source['edges']}
    live = {e['id']: e for e in current['edges']}
    nodes = {n['id']: n for n in current['nodes']}
    geometry_keys = {'from', 'to', 'pathMeters', 'generationHistory'}
    local_endpoints = set()
    for approach in candidate['approaches']:
        raw = raw_edges[approach['edgeId']]
        key = 'from' if raw['from'] == raw_node else 'to'
        opposite = 'to' if key == 'from' else 'from'
        index = 0 if key == 'from' else -1
        for expected in baseline_walks.get(raw['id'], []):
            local_endpoints.add(expected[key])
            actual = live.get(expected['id'])
            if actual is None:
                return True
            if {k: v for k, v in actual.items() if k not in geometry_keys} != {
                    k: v for k, v in expected.items() if k not in geometry_keys}:
                return True
            if (actual[key] != expected[key] or len(actual['pathMeters']) != len(expected['pathMeters'])
                    or actual['pathMeters'][index] != expected['pathMeters'][index]
                    or nodes.get(actual[key]) != baseline_nodes.get(expected[key])):
                return True
            if actual[opposite] != expected[opposite] or actual['pathMeters'] != expected['pathMeters']:
                # Only allow a documented automatic modification of the far end.
                if not any(raw[opposite] in item.get('rawNodeIds', [])
                           for item in actual.get('generationHistory', [])):
                    return True
                if len(actual['pathMeters']) != 2:
                    return True
    # A deleted/reshaped connection is an intentional edit too: rebuilding a
    # corner here must not silently reinstate a connection the user removed.
    for edge in baseline_edges.values():
        if edge['kind'] != 'walkway' and (edge['from'] in local_endpoints or edge['to'] in local_endpoints):
            if live.get(edge['id']) != edge:
                return True
    return False


def generate_network_junctions(source: dict, annotations: dict, graph: dict, *,
                               selected_candidate_ids: list[str] | None = None,
                               crossing_choices: dict[str, list[str]] | None = None) -> dict:
    """Generate selected ordinary junctions in a copy, preserving existing edits.

    None selects every eligible candidate; [] only previews candidates. Existing
    crossings are never silently removed. Save the returned nodes/edges using
    the existing editor patch path, not by changing the converter's base.
    """
    if source.get('coordinateSystem') != 'preview-local-v1' or annotations.get('authoringMode') == 'engine-snapshot':
        raise ValueError('此区域缺少可识别的道路源图；请先导入含源拓扑的道路数据')
    prepared = _prepare_source(source, annotations)
    origin = graph['originWgs84']
    coordinates = [origin['lng'], origin['lat']] if isinstance(origin, dict) else origin
    reference = convert_preview_graph(with_preview_annotations(source, annotations), coordinates,
                                      annotations['crossings'], annotations.get('junctions', []))
    draft = normalize_graph(deepcopy(graph))
    protected, generated = _protection(prepared, annotations, draft, reference)
    candidates = detect_junctions(prepared, protected_node_ids=protected)
    selected = None if selected_candidate_ids is None else set(selected_candidate_ids)
    choices = crossing_choices or {}
    identities = {c['id'] for c in candidates}
    if (selected is not None and not selected <= identities) or not set(choices) <= identities:
        raise ValueError('所选路口已变化，请重新生成预览')
    reference_walks = _walk_index(reference, prepared)
    reference_nodes = {n['id']: n for n in reference['nodes']}
    reference_edges = {e['id']: e for e in reference['edges']}
    for candidate in candidates:
        raw_node = candidate['rawNodeIds'][0]
        candidate['crossings'] = []
        if raw_node in generated:
            candidate.update(status='preserved', reason='already_generated')
        elif candidate['status'] == 'ready':
            if _changed_approach(candidate, prepared, draft, reference_walks, reference_nodes, reference_edges):
                candidate.update(status='preserved', reason='manual_edit_preserved')
            else:
                try:
                    proposal = build_junction_proposal(prepared, draft, candidate)
                    existing = {e['id']: e for e in draft['edges']}
                    if any(existing[eid] != reference_edges.get(eid)
                           for eid in proposal['annotation']['retireEdgeIds']):
                        candidate.update(status='preserved', reason='manual_edit_preserved')
                    elif any(existing[eid]['kind'] == 'crossing' for eid in proposal['annotation']['retireEdgeIds']):
                        candidate.update(status='pending', reason='existing_crossing_preserved')
                    else:
                        crossing_ids = tuple(choices.get(candidate['id'], []))
                        staged, record = apply_junction_proposal(draft, proposal, selected_crossing_ids=crossing_ids)
                        nodes = {n['id']: n for n in staged['nodes']}
                        ports = {p['id']: p['nodeId'] for p in record['ports']}
                        for link in proposal['annotation']['connectors']:
                            if link['kind'] == 'crossing':
                                path = [[nodes[ports[link[k]]]['xMeters'], nodes[ports[link[k]]]['yMeters']]
                                        for k in ('fromPort', 'toPort')]
                                candidate['crossings'].append({**link, 'pathMeters': path,
                                                               'selected': link['id'] in crossing_ids})
                        if selected is None or candidate['id'] in selected:
                            draft = staged
                            candidate.update(status='generated', reason=None)
                            candidate['record'] = record
                except ValueError as exc:
                    candidate.update(status='pending', reason='template_mapping_review', detail=str(exc))
        candidate['reasonText'] = REASONS.get(candidate['reason'], candidate['reason'] or '可生成普通路口模板')
        # The full proposal is internal; frontend only needs the review data.
        candidate.pop('proposal', None)
    counts = Counter(c['status'] for c in candidates)
    return {'graph': draft, 'candidates': candidates, 'summary': {
        'candidateCount': len(candidates), 'generated': counts['generated'],
        'preserved': counts['preserved'], 'pending': counts['pending'], 'ready': counts['ready'],
        'crossingCandidates': sum(len(c['crossings']) for c in candidates),
        'selectedCrossings': sum(x['selected'] for c in candidates for x in c['crossings']),
    }}
