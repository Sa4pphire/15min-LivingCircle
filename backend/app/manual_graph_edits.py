"""Replayable, revision-guarded edits to the generated synthetic walking graph."""

from copy import deepcopy
import hashlib
import json
import math

from .network_audit import audit_network
from .walking_types import normalize_graph, legacy_graph_view


def graph_revision(graph: dict) -> str:
    payload = {key: sorted(graph[key], key=lambda item: item['id'])
               for key in ('nodes', 'edges')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def validate_editor_graph(graph: dict) -> dict:
    graph = normalize_graph(graph)
    if graph.get('authoringWorkspace') is True and not graph.get('edges'):
        if graph.get('facilities'):
            raise ValueError('空白路网不能包含设施入口')
        return audit_network(graph)
    if not graph.get('nodes') or not graph.get('edges'):
        raise ValueError('路网至少需要一个可步行路段')
    if not any(edge.get('kind') == 'walkway' for edge in graph['edges']):
        raise ValueError('路网至少需要一条 walkway 步行通道')
    report = audit_network(graph)
    graph = legacy_graph_view(graph)
    block_kinds, node_sides = {}, {}
    for edge in graph['edges']:
        if not isinstance(edge.get('id'), str) or not edge['id']:
            raise ValueError('路段 ID 不能为空')
        kind, block = edge['kind'], edge.get('streetBlockId')
        if len(edge['pathMeters']) > 1000:
            raise ValueError(f"路段形状点不能超过 1000 个: {edge['id']}")
        if 'waitSeconds' in edge and kind != 'crossing':
            raise ValueError(f"只有过街边可以设置等待: {edge['id']}")
        if kind in ('sidewalk', 'shared_way'):
            if not isinstance(block, str) or not block:
                raise ValueError(f"路段缺少道路组: {edge['id']}")
            if block in block_kinds and block_kinds[block] != kind:
                raise ValueError(f'道路组不能混用人行道和共享步道: {block}')
            block_kinds[block] = kind
        if kind == 'sidewalk':
            if edge.get('side') not in ('left', 'right'):
                raise ValueError(f"人行道缺少左右侧: {edge['id']}")
            for node_id in (edge['from'], edge['to']):
                sides = node_sides.setdefault(node_id, {})
                if block in sides and sides[block] != edge['side']:
                    raise ValueError(f'同一道路两侧不能共用节点: {node_id}')
                sides[block] = edge['side']
        if kind == 'shared_way':
            width = edge.get('widthMeters')
            if (edge.get('side') or edge.get('sharedWayType') not in
                    ('pedestrian_street', 'shared_alley') or isinstance(width, bool) or
                    not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0):
                raise ValueError(f"共享步道的类型或宽度无效: {edge['id']}")
        elif edge.get('sharedWayType') or edge.get('widthMeters', 0) != 0:
            raise ValueError(f"非共享步道不能设置共享宽度: {edge['id']}")
    for edge in graph['edges']:
        if edge['kind'] != 'turn':
            continue
        start, end = node_sides.get(edge['from'], {}), node_sides.get(edge['to'], {})
        if any(block in end and end[block] != side for block, side in start.items()):
            raise ValueError(f"跨越同一道路两侧请使用过街连接: {edge['id']}")
    edge_ids = {edge['id'] for edge in graph['edges']}
    for facility in graph.get('facilities', []):
        for entrance in facility.get('entrances', [facility]):
            if entrance.get('accessEdgeId') not in edge_ids:
                raise ValueError('不能删除仍被设施入口引用的路段')
    boundary_ids = graph.get('localExperiment', {}).get('boundaryNodeIds', [])
    if not set(boundary_ids) <= {node['id'] for node in graph['nodes']}:
        raise ValueError('不能删除仍被裁剪出口引用的节点')
    return report


def make_manual_edits(base: dict, edited: dict) -> dict:
    base, edited = normalize_graph(base), normalize_graph(edited)
    validate_editor_graph(edited)
    patch = {'schemaVersion': 1, 'coordinateSystem': 'engine-local-meters',
             'baseGraphRevision': graph_revision(base),
             'verificationStatus': 'user_edited_unverified'}
    for key in ('nodes', 'edges'):
        original = {item['id']: item for item in base[key]}
        current = {item['id']: item for item in edited[key]}
        patch['removed' + key.title()] = sorted(original.keys() - current.keys())
        patch['upsert' + key.title()] = [current[item_id] for item_id in sorted(current)
                                       if current[item_id] != original.get(item_id)]
    return patch


def apply_manual_edits(base: dict, patch: dict | None) -> dict:
    base = normalize_graph(base)
    if not patch:
        return base
    if (patch.get('schemaVersion') != 1 or
            patch.get('coordinateSystem') != 'engine-local-meters'):
        raise ValueError('人工路网编辑记录格式无效')
    if patch.get('baseGraphRevision') != graph_revision(base):
        raise ValueError('源路网已变化，人工编辑需重新核对；为防止错连，本次未覆盖路网')
    graph = deepcopy(base)
    for key in ('nodes', 'edges'):
        items = {item['id']: item for item in graph[key]}
        for item_id in patch['removed' + key.title()]:
            if item_id not in items:
                raise ValueError(f'人工编辑引用已失效: {item_id}')
            del items[item_id]
        for item in patch['upsert' + key.title()]:
            items[item['id']] = deepcopy(item)
        graph[key] = list(items.values())
    graph = normalize_graph(graph)
    validate_editor_graph(graph)
    # Historical source annotations remain provenance, not routing references.
    graph.setdefault('sourceGraph', {})['manualGraphEdits'] = {
        'verificationStatus': 'user_edited_unverified',
        'baseGraphRevision': patch['baseGraphRevision'],
        'removedNodeIds': patch['removedNodes'], 'removedEdgeIds': patch['removedEdges'],
        'editedNodeIds': [item['id'] for item in patch['upsertNodes']],
        'editedEdgeIds': [item['id'] for item in patch['upsertEdges']],
    }
    return graph
