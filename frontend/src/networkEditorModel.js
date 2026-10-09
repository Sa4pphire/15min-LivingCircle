export const kinds = {
  walkway: { label: '步行通道 · walkway', color: '#187b8c' },
  turn: { label: '转弯连接', color: '#765ca5' },
  crossing: { label: '过街连接', color: '#d78024' },
};
export const xy = node => [node.xMeters, node.yMeters];
export const clone = value => JSON.parse(JSON.stringify(value));
export const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
export const pathLength = points => points.slice(1).reduce((sum, p, i) => sum + distance(points[i], p), 0);
export const newId = type => `manual:${type}:${crypto.randomUUID()}`;

export function normalizeEditorEdge(edge) {
  if (edge.kind === 'sidewalk' || edge.kind === 'shared_way') {
    const accessMode = edge.kind === 'sidewalk' ? 'separated' : 'shared';
    if (edge.accessMode && edge.accessMode !== accessMode) throw new Error('旧路段类型与接入方式冲突');
    return { ...edge, kind: 'walkway', accessMode };
  }
  if (edge.kind === 'walkway' && !['separated', 'shared'].includes(edge.accessMode)) {
    throw new Error('步行通道需要明确接入方式');
  }
  if (!kinds[edge.kind]) throw new Error('路段类型无效');
  return edge;
}

export function normalizeEditorGraph(graph) {
  if (!Array.isArray(graph.edges)) throw new Error('路网缺少 edges 数组');
  return { ...graph, edges: graph.edges.map(normalizeEditorEdge) };
}

export function moveNode(graph, id, point) {
  const node = graph.nodes.find(n => n.id === id);
  if (!node || !Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite)) throw new Error('节点坐标无效');
  graph.nodes = graph.nodes.map(item => item.id === id ? { ...item, xMeters: point[0], yMeters: point[1] } : item);
  graph.edges = graph.edges.map(edge => edge.from !== id && edge.to !== id ? edge : {
    ...edge, pathMeters: edge.pathMeters.map((p, index) =>
      (index === 0 && edge.from === id) || (index === edge.pathMeters.length - 1 && edge.to === id) ? [...point] : p) });
}

export function editShapePoint(graph, id, index, point, insert = false) {
  const edge = graph.edges.find(item => item.id === id);
  if (!edge || !Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite) ||
      index <= 0 || index >= edge.pathMeters.length || (!insert && index === edge.pathMeters.length - 1)) throw new Error('形状点无效');
  const path = edge.pathMeters.slice();
  if (insert) path.splice(index, 0, [...point]); else path[index] = [...point];
  graph.edges = graph.edges.map(item => item.id === id ? { ...item, pathMeters: path } : item);
}

export function edgeRemovalPlan(graph, ids) {
  const chosen = new Set(ids), available = new Set(graph.edges.map(edge => edge.id));
  if ([...chosen].some(id => !available.has(id))) throw new Error('选中的路段已失效，请重新选择');
  const removed = graph.edges.filter(edge => chosen.has(edge.id));
  const candidates = new Set(removed.flatMap(edge => [edge.from, edge.to]));
  const remaining = new Set(graph.edges.filter(edge => !chosen.has(edge.id)).flatMap(edge => [edge.from, edge.to]));
  return { edgeIds: removed.map(edge => edge.id),
    nodeIds: graph.nodes.filter(node => candidates.has(node.id) && !remaining.has(node.id)).map(node => node.id) };
}

function applyRemoval(graph, plan) {
  const removedNodes = new Set(plan.nodeIds), removedEdges = new Set(plan.edgeIds);
  if (graph.localExperiment?.boundaryNodeIds?.some(id => removedNodes.has(id))) throw new Error('被删除的节点包含裁剪出口，请先处理出口引用');
  if (graph.facilities?.some(facility => (facility.entrances ?? [facility]).some(entrance => removedEdges.has(entrance.accessEdgeId)))) {
    throw new Error('相连路段绑定了设施入口，请先处理入口引用');
  }
  graph.edges = graph.edges.filter(edge => !removedEdges.has(edge.id));
  graph.nodes = graph.nodes.filter(node => !removedNodes.has(node.id));
  return plan;
}

export function removeEdges(graph, ids) {
  return applyRemoval(graph, edgeRemovalPlan(graph, ids));
}

export function removeNode(graph, id) {
  if (!graph.nodes.some(node => node.id === id)) throw new Error('节点不存在，请重新选择');
  const plan = edgeRemovalPlan(graph, graph.edges.filter(edge => edge.from === id || edge.to === id).map(edge => edge.id));
  plan.nodeIds = [...new Set([id, ...plan.nodeIds])];
  return { nodeId: id, ...applyRemoval(graph, plan) };
}

export function projectToPath(point, points) {
  let best = null;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    const length2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2;
    const t = length2 ? Math.max(0, Math.min(1,
      ((point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1])) / length2)) : 0;
    const projected = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
    const d = distance(point, projected);
    if (!best || d < best.distance) best = { point: projected, index: i, t, distance: d };
  }
  return best;
}

export function splitEdge(graph, id, point, nodeId = newId('node'), edgeId = newId('edge')) {
  const edge = graph.edges.find(e => e.id === id);
  if (!edge) throw new Error('路段不存在');
  if (edge.kind === 'crossing') throw new Error('过街拆分会重复计等待；请单独重画过街连接');
  if (graph.facilities?.some(f => (f.entrances ?? [f]).some(e => e.accessEdgeId === id))) {
    throw new Error('该路段绑定设施入口，请先处理入口引用');
  }
  const projection = projectToPath(point, edge.pathMeters);
  const first = edge.pathMeters.slice(0, projection.index);
  const second = edge.pathMeters.slice(projection.index);
  if (distance(first.at(-1), projection.point) > 1e-9) first.push(projection.point);
  if (distance(second[0], projection.point) > 1e-9) second.unshift(projection.point);
  if (pathLength(first) < 0.05 || pathLength(second) < 0.05) throw new Error('请在路段内部拆分');
  graph.nodes.push({ id: nodeId, xMeters: projection.point[0], yMeters: projection.point[1],
    verificationStatus: 'user_edited_unverified' });
  const originalTo = edge.to;
  graph.edges = graph.edges.map(item => item.id === id ? { ...item, to: nodeId, pathMeters: clone(first) } : item);
  graph.edges.push({ ...clone(edge), id: edgeId, from: nodeId, to: originalTo,
    splitFromEdgeId: id, pathMeters: clone(second), verificationStatus: 'user_edited_unverified' });
  return nodeId;
}

export function edgeProperties(edge, values) {
  if (!kinds[values.kind]) throw new Error('路段类型无效');
  const updated = { ...edge, kind: values.kind, verificationStatus: 'user_edited_unverified' };
  for (const key of ['streetBlockId', 'side', 'widthMeters', 'sharedWayType', 'waitSeconds', 'accessMode']) delete updated[key];
  if (values.kind === 'walkway') {
    updated.streetBlockId = values.streetBlockId?.trim() || `manual-block:${edge.id}`;
    const mode = values.accessMode ?? (edge.side ? 'separated' : 'shared');
    if (!['separated', 'shared'].includes(mode)) throw new Error('接入方式无效');
    updated.accessMode = mode;
    if (mode === 'separated') {
      const side = values.side || 'left';
      if (!['left', 'right'].includes(side)) throw new Error('道路侧必须为左侧或右侧');
      updated.side = side;
    } else {
      const width = Number(values.widthMeters);
      if (!Number.isFinite(width) || width <= 0) throw new Error('步行空间宽度必须大于 0');
      const type = values.sharedWayType || 'shared_alley';
      if (!['shared_alley', 'pedestrian_street'].includes(type)) throw new Error('共享空间类别无效');
      updated.widthMeters = width;
      updated.sharedWayType = type;
    }
  }
  if (values.kind === 'crossing') {
    const wait = Number(values.waitSeconds);
    if (!Number.isFinite(wait) || wait < 0) throw new Error('等待时间必须为非负数');
    updated.waitSeconds = wait;
  }
  return updated;
}

// Use segment clipping, so a line through the box is selected even when both
// endpoints are outside, and a diagonal bounding-box overlap alone is not enough.
export function edgesInRectangle(edges, first, second, visibleKinds) {
  const minimum = [0, 1].map(axis => Math.min(first[axis], second[axis]));
  const maximum = [0, 1].map(axis => Math.max(first[axis], second[axis]));
  function intersects(a, b) {
    let low = 0, high = 1;
    for (let axis = 0; axis < 2; axis++) {
      const delta = b[axis] - a[axis];
      if (delta === 0) {
        if (a[axis] < minimum[axis] || a[axis] > maximum[axis]) return false;
      } else {
        const t1 = (minimum[axis] - a[axis]) / delta, t2 = (maximum[axis] - a[axis]) / delta;
        low = Math.max(low, Math.min(t1, t2)); high = Math.min(high, Math.max(t1, t2));
        if (low > high) return false;
      }
    }
    return true;
  }
  return edges.filter(edge => (!visibleKinds || visibleKinds[edge.kind]) &&
    edge.pathMeters.slice(1).some((point, index) => intersects(edge.pathMeters[index], point))).map(edge => edge.id);
}

export function updateEdges(graph, ids, values) {
  const chosen = new Set(ids);
  const available = new Set(graph.edges.map(edge => edge.id));
  if (!chosen.size || [...chosen].some(id => !available.has(id))) throw new Error('选中的路段已失效，请重新选择');
  if (values.kind && !kinds[values.kind]) throw new Error('路段类型无效');
  const hasWait = values.waitSeconds !== undefined && values.waitSeconds !== '';
  const wait = Number(values.waitSeconds);
  if (hasWait && (!Number.isFinite(wait) || wait < 0)) throw new Error('等待时间必须为非负数');
  let changed = 0;
  const updated = graph.edges.map(edge => {
    if (!chosen.has(edge.id)) return edge;
    const kindChanged = Boolean(values.kind && values.kind !== edge.kind);
    let result = kindChanged ? edgeProperties(edge, { ...edge, kind: values.kind,
      waitSeconds: edge.waitSeconds ?? 20, widthMeters: edge.widthMeters ?? 4 }) : edge;
    if (hasWait && result.kind === 'crossing') result = { ...result, waitSeconds: wait };
    if (result === edge) return edge;
    changed++;
    return { ...result, verificationStatus: 'user_edited_unverified' };
  });
  if (!changed) throw new Error('所选路段没有需要更新的属性；等待时间仅用于过街连接');
  graph.edges = updated;
  return changed;
}

// Geometric intersections never add adjacency. Waits influence the chosen route.
export function findRoute(graph, start, end, speed = 1.3) {
  const adjacency = new Map(graph.nodes.map(n => [n.id, []]));
  for (const edge of graph.edges) {
    const cost = pathLength(edge.pathMeters) / speed + (edge.kind === 'crossing' ? edge.waitSeconds ?? 20 : 0);
    adjacency.get(edge.from)?.push({ to: edge.to, edge, cost });
    adjacency.get(edge.to)?.push({ to: edge.from, edge, cost });
  }
  const costs = new Map([[start, 0]]), previous = new Map(), heap = [[0, start]];
  function push(item) {
    heap.push(item);
    let i = heap.length - 1;
    while (i && heap[(i - 1) >> 1][0] > item[0]) {
      const parent = (i - 1) >> 1;
      heap[i] = heap[parent]; i = parent;
    }
    heap[i] = item;
  }
  function pop() {
    const first = heap[0], last = heap.pop();
    if (heap.length) {
      let i = 0;
      while (i * 2 + 1 < heap.length) {
        let child = i * 2 + 1;
        if (child + 1 < heap.length && heap[child + 1][0] < heap[child][0]) child++;
        if (heap[child][0] >= last[0]) break;
        heap[i] = heap[child]; i = child;
      }
      heap[i] = last;
    }
    return first;
  }
  while (heap.length) {
    const [cost, node] = pop();
    if (cost !== costs.get(node)) continue;
    if (node === end) break;
    for (const step of adjacency.get(node) ?? []) {
      const next = cost + step.cost;
      if (next < (costs.get(step.to) ?? Infinity)) {
        costs.set(step.to, next); previous.set(step.to, { from: node, edge: step.edge });
        push([next, step.to]);
      }
    }
  }
  if (!costs.has(end)) return null;
  const edges = [];
  for (let node = end; node !== start;) {
    const step = previous.get(node); edges.unshift(step.edge); node = step.from;
  }
  return { seconds: costs.get(end), edges,
    waitSeconds: edges.reduce((sum, e) => sum + (e.kind === 'crossing' ? e.waitSeconds ?? 20 : 0), 0) };
}
