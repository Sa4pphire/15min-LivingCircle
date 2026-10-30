"""Budgeted, single-input sensitivity analysis for local model review.

The scenarios describe assumptions on a frozen walking graph, not measured
access, confidence intervals or global bounds. No graph or input is modified.
"""
from heapq import heappop, heappush
import math


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _options(budget, threshold, speed, low_wait, high_wait):
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 30:
        raise ValueError('精化评估预算需为1至30个候选')
    if not _finite(threshold) or not 0 < threshold <= 3600:
        raise ValueError('精化时间阈值需大于0且不超过3600秒')
    if not _finite(speed) or not 0 < speed <= 3:
        raise ValueError('步速需大于0且不超过3米每秒')
    if (not _finite(low_wait) or not _finite(high_wait)
            or not 0 <= low_wait <= high_wait <= 300):
        raise ValueError('等待情景需满足0 ≤ 较短等待 ≤ 较长等待 ≤ 300秒')


def _adjacency(graph, speed):
    nodes = {n['id']: n for n in graph['nodes']}
    edges = {e['id']: e for e in graph['edges']}
    adjacency = {nid: [] for nid in nodes}
    for edge in graph['edges']:
        path = edge['pathMeters']
        walk = sum(math.dist(a, b) for a, b in zip(path, path[1:])) / speed
        wait = edge.get('waitSeconds', 20) if edge['kind'] == 'crossing' else 0
        if not _finite(walk) or walk < 0 or not _finite(wait) or wait < 0:
            raise ValueError('当前路段包含无效的走行时间或等待时间')
        a, b = edge['from'], edge['to']
        adjacency[a].append((b, walk, wait, edge['id']))
        adjacency[b].append((a, walk, wait, edge['id']))
    return nodes, edges, adjacency


def _distances(adjacency, start, *, replacement_id=None, replacement_wait=None, addition=None, excluded_id=None):
    """Dijkstra with one virtual edge/cost change; no shared adjacency mutation."""
    distances, queue = {start: 0.0}, [(0.0, start)]
    while queue:
        seconds, node = heappop(queue)
        if seconds != distances[node]:
            continue
        for other, walk, wait, edge_id in adjacency[node]:
            if edge_id == excluded_id:
                continue
            cost = walk + (replacement_wait if edge_id == replacement_id else wait)
            candidate = seconds + cost
            if candidate < distances.get(other, math.inf):
                distances[other] = candidate
                heappush(queue, (candidate, other))
        if addition is not None:
            a, b, cost = addition
            other = b if node == a else a if node == b else None
            if other is not None and seconds + cost < distances.get(other, math.inf):
                distances[other] = seconds + cost
                heappush(queue, (seconds + cost, other))
    return distances


def _model(item, nodes, edges, speed):
    if not item.get('resolvable'):
        return None, '尚无可应用的局部方案，需补充连接与来源'
    kind = item['kind']
    if kind == 'missing_crossing':
        ports = item.get('nodeIds', [])
        if (len(ports) != 2 or ports[0] == ports[1]
                or any(nid not in nodes for nid in ports)):
            return None, '过街两端的实际端口不完整，需重新生成候选'
        a, b = (nodes[nid] for nid in ports)
        length = math.dist((a['xMeters'], a['yMeters']), (b['xMeters'], b['yMeters']))
        if not math.isfinite(length) or length < .05:
            return None, '候选过街长度无效，需核查端口位置'
        current_id = item.get('edgeIds', [None])[0] if item.get('edgeIds') else None
        current = edges.get(current_id)
        if current and (current['kind'] != 'crossing' or set((current['from'], current['to'])) != set(ports)):
            return None, '原过街的端点已变化，需要重新核对'
        return {'kind': kind, 'nodeIds': ports, 'walkSeconds': length / speed,
                'existingEdgeId': current_id}, None
    if kind == 'crossing_wait':
        ids = item.get('edgeIds', [])
        edge = edges.get(ids[0]) if len(ids) == 1 else None
        if edge is None or edge['kind'] != 'crossing':
            return None, '对应过街路段已变化，需重新生成候选'
        return {'kind': kind, 'nodeIds': [edge['from'], edge['to']], 'edgeId': edge['id']}, None
    return None, '入口或复杂路口缺少明确替代连接，暂不估算影响'


def _screening(model, baseline, threshold):
    arrivals = [baseline[nid] for nid in model['nodeIds'] if nid in baseline]
    earliest = min(arrivals) if arrivals else None
    gap = min(abs(t - threshold) for t in arrivals) if arrivals else None
    missing = sum(nid not in baseline for nid in model['nodeIds'])
    accessible = earliest is not None and earliest <= threshold
    return {'earliestEndpointSeconds': round(earliest, 6) if earliest is not None else None,
            'boundaryGapSeconds': round(gap, 6) if gap is not None else None,
            'unreachableEndpointCount': missing,
            'endpointWithinThreshold': accessible,
            'method': 'baseline_endpoint_and_boundary_heuristic'}


def _scenario(distances, threshold):
    return {'reachableNodeCount': sum(t <= threshold for t in distances.values()),
            'connectedNodeCount': len(distances)}


def _impact(optimistic, pessimistic, baseline, threshold):
    low = {nid for nid, t in optimistic.items() if t <= threshold}
    high = {nid for nid, t in pessimistic.items() if t <= threshold}
    current = {nid for nid, t in baseline.items() if t <= threshold}
    relevant = low | high
    gaps = [nid for nid in relevant if (nid in optimistic) != (nid in pessimistic)]
    finite_deltas = [abs(optimistic[nid] - pessimistic[nid]) for nid in relevant
                     if nid in optimistic and nid in pessimistic]
    finite_max = round(max(finite_deltas), 6) if finite_deltas else None
    changed = low ^ high
    return {'changedReachabilityNodeCount': len(changed),
            'optimisticAddedNodeCount': len(low - high),
            'optimisticVsBaselineChangedNodeCount': len(low ^ current),
            'pessimisticVsBaselineChangedNodeCount': len(high ^ current),
            # Disconnection has no finite travel-time delta. Keep its count
            # separate so an origin-only finite comparison cannot hide it.
            'reachableGapNodeCount': len(gaps),
            'maxTravelTimeDeltaSeconds': None if gaps else finite_max,
            'maxFiniteTravelTimeDeltaSeconds': finite_max,
            'changedNodeSample': sorted(changed)[:20]}


def plan_refinements(graph, inputs, origin_node_id, *, budget=10,
                     threshold_seconds=900, walking_speed=1.3,
                     low_wait_seconds=0, high_wait_seconds=60):
    """Choose a bounded batch, then rank its measured *model sensitivity*.

    The heuristic first favours an endpoint already within the threshold,
    then a link to a currently disconnected endpoint, then boundary proximity.
    Only evaluated items receive numeric ranks. Every scenario changes one
    input in isolation; effects cannot be added or read as global bounds.
    """
    _options(budget, threshold_seconds, walking_speed, low_wait_seconds, high_wait_seconds)
    nodes, edges, adjacency = _adjacency(graph, walking_speed)
    if origin_node_id not in nodes:
        raise ValueError('精化分析起点已变化，请重新选择一个实际路网节点')
    baseline = _distances(adjacency, origin_node_id)
    items, eligible = [], []
    for original in inputs:
        item = {**original, 'priority': None, 'impact': None, 'scenario': None, 'screening': None}
        items.append(item)
        if item.get('review'):
            item.update(evaluationStatus='reviewed', evaluationReason='已记录核查选择，可在编辑器中重新审阅')
            continue
        model, issue = _model(item, nodes, edges, walking_speed)
        if model is None:
            item.update(evaluationStatus='not_evaluable', evaluationReason=issue)
            continue
        screening = _screening(model, baseline, threshold_seconds)
        item.update(evaluationStatus='budget_not_evaluated',
                    evaluationReason='未进入本轮预算，尚无影响评估结果', screening=screening)
        # A missing endpoint can hide a large component even when the known
        # endpoint is well inside the boundary. Neither feature is a confidence.
        key = (not screening['endpointWithinThreshold'],
               -screening['unreachableEndpointCount'] if screening['endpointWithinThreshold'] else 0,
               screening['boundaryGapSeconds'] if screening['boundaryGapSeconds'] is not None else math.inf,
               item['id'])
        eligible.append((key, item, model))
    for _, item, model in sorted(eligible, key=lambda entry: entry[0])[:budget]:
        if model['kind'] == 'missing_crossing':
            a, b = model['nodeIds']
            if model['existingEdgeId'] is not None:
                optimistic = _distances(adjacency, origin_node_id,
                    replacement_id=model['existingEdgeId'], replacement_wait=low_wait_seconds)
                pessimistic = _distances(adjacency, origin_node_id, excluded_id=model['existingEdgeId'])
            else:
                optimistic = _distances(adjacency, origin_node_id,
                    addition=(a, b, model['walkSeconds'] + low_wait_seconds))
                pessimistic = baseline  # This missing connection remains closed.
            scenario = {'optimistic': {**_scenario(optimistic, threshold_seconds),
                                      'connection': 'open', 'waitSeconds': low_wait_seconds},
                        'pessimistic': {**_scenario(pessimistic, threshold_seconds), 'connection': 'closed'}}
        else:
            optimistic = _distances(adjacency, origin_node_id,
                replacement_id=model['edgeId'], replacement_wait=low_wait_seconds)
            pessimistic = _distances(adjacency, origin_node_id,
                replacement_id=model['edgeId'], replacement_wait=high_wait_seconds)
            scenario = {'optimistic': {**_scenario(optimistic, threshold_seconds), 'waitSeconds': low_wait_seconds},
                        'pessimistic': {**_scenario(pessimistic, threshold_seconds), 'waitSeconds': high_wait_seconds}}
        item.update(evaluationStatus='evaluated', evaluationReason='已比较本候选的两个独立局部情景',
                    scenario=scenario, impact=_impact(optimistic, pessimistic, baseline, threshold_seconds))
    evaluated = [item for item in items if item['evaluationStatus'] == 'evaluated']
    evaluated.sort(key=lambda item: (-item['impact']['changedReachabilityNodeCount'],
        -item['impact']['reachableGapNodeCount'],
        -(item['impact']['maxFiniteTravelTimeDeltaSeconds'] or 0), item['id']))
    for rank, item in enumerate(evaluated, 1):
        item['priority'] = rank
    order = {'evaluated': 0, 'budget_not_evaluated': 1, 'not_evaluable': 2, 'reviewed': 3}
    items.sort(key=lambda item: (order[item['evaluationStatus']], item['priority'] or 0, item['id']))
    return {'items': items,
            'baseline': {'originNodeId': origin_node_id, 'totalNodeCount': len(nodes),
                         'thresholdSeconds': threshold_seconds, 'walkingSpeedMetersPerSecond': walking_speed,
                         **_scenario(baseline, threshold_seconds)},
            'summary': {'candidateCount': len(items), 'budget': budget, 'evaluatedCount': len(evaluated),
                        'notEvaluatedCount': sum(x['evaluationStatus'] == 'budget_not_evaluated' for x in items),
                        'notEvaluableCount': sum(x['evaluationStatus'] == 'not_evaluable' for x in items),
                        'reviewedCount': sum(x['evaluationStatus'] == 'reviewed' for x in items)},
            'parameters': {'lowWaitSeconds': low_wait_seconds, 'highWaitSeconds': high_wait_seconds},
            'assumptions': [
                '排序是当前起点、当前路网和情景参数下的核查线索，不是统计置信概率或真实改造收益。',
                '每次只改变一个局部输入；两情景不是全图上下界，多个候选共同变化可能产生额外影响。',
                '变化量按阈值内实际图节点集合的对称差计算，不是覆盖面积、设施覆盖数或人口。',
                '等待范围为用户设置的情景参数，不能当作实测值或经校准的误差区间。',
                '未评估或暂不可评估不等于没有影响；节点密度不同会影响节点变化计数。',
            ]}
