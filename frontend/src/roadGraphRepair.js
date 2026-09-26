// Conservative repairs for the SVG-derived *synthetic* graph. These are never
// treated as verified pedestrian crossings or sidewalk connections.

const DEFAULT_SNAP_METERS = 1.5;
const MIN_T_ANGLE_SINE = Math.sin(25 * Math.PI / 180);

function pointId(x, y) {
  return `p:${x.toFixed(1)}:${y.toFixed(1)}`;
}

function componentsFor(graph, adjacency) {
  const component = new Map();
  let count = 0;
  for (const node of graph.nodes) {
    if (component.has(node.id)) continue;
    const queue = [node.id];
    component.set(node.id, count);
    for (let index = 0; index < queue.length; index += 1) {
      for (const edge of adjacency.get(queue[index])) {
        const next = edge.from === queue[index] ? edge.to : edge.from;
        if (!component.has(next)) {
          component.set(next, count);
          queue.push(next);
        }
      }
    }
    count += 1;
  }
  return component;
}

function projectOnSegment(point, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared < 1e-8) return null;
  const t = Math.max(0, Math.min(1,
    ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared));
  const x = a.x + dx * t;
  const y = a.y + dy * t;
  return { x, y, t, distance: Math.hypot(point.x - x, point.y - y), length: Math.sqrt(lengthSquared) };
}

function edgeOf(nodeA, nodeB, original, id) {
  return {
    ...original,
    id,
    from: nodeA.id,
    to: nodeB.id,
    length: Number(Math.hypot(nodeB.x - nodeA.x, nodeB.y - nodeA.y).toFixed(1)),
  };
}

export function repairDemoRoadGraph(graph, { snapMeters = DEFAULT_SNAP_METERS } = {}) {
  if (!Number.isFinite(snapMeters) || snapMeters <= 0 || snapMeters > 2) {
    throw new Error("INVALID_SNAP_TOLERANCE");
  }
  const nodes = new Map(graph.nodes.map((node) => [node.id, { ...node }]));
  const adjacency = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) {
    adjacency.get(edge.from).push(edge);
    adjacency.get(edge.to).push(edge);
  }
  const component = componentsFor(graph, adjacency);
  const cellSize = 20;
  const cell = (value) => Math.floor(value / cellSize);
  const key = (x, y) => `${x}:${y}`;
  const index = new Map();
  for (const edge of graph.edges) {
    const a = nodes.get(edge.from);
    const b = nodes.get(edge.to);
    for (let x = cell(Math.min(a.x, b.x) - snapMeters); x <= cell(Math.max(a.x, b.x) + snapMeters); x += 1) {
      for (let y = cell(Math.min(a.y, b.y) - snapMeters); y <= cell(Math.max(a.y, b.y) + snapMeters); y += 1) {
        const id = key(x, y);
        if (!index.has(id)) index.set(id, []);
        index.get(id).push(edge);
      }
    }
  }

  const candidates = [];
  for (const node of graph.nodes) {
    const incident = adjacency.get(node.id);
    if (incident.length !== 1) continue;
    const ownEdge = incident[0];
    const neighbor = nodes.get(ownEdge.from === node.id ? ownEdge.to : ownEdge.from);
    const ownDx = node.x - neighbor.x;
    const ownDy = node.y - neighbor.y;
    const ownLength = Math.hypot(ownDx, ownDy);
    const checked = new Set();
    let best = null;
    for (let x = cell(node.x - snapMeters); x <= cell(node.x + snapMeters); x += 1) {
      for (let y = cell(node.y - snapMeters); y <= cell(node.y + snapMeters); y += 1) {
        for (const edge of index.get(key(x, y)) ?? []) {
          if (checked.has(edge.id) || edge.sourceWayId === ownEdge.sourceWayId ||
            edge.from === node.id || edge.to === node.id ||
            component.get(edge.from) === component.get(node.id)) continue;
          checked.add(edge.id);
          const a = nodes.get(edge.from);
          const b = nodes.get(edge.to);
          const projected = projectOnSegment(node, a, b);
          if (!projected || projected.distance > snapMeters) continue;
          const nearEndpoint = Math.min(projected.t, 1 - projected.t) * projected.length <= snapMeters;
          const sine = Math.abs(ownDx * (b.y - a.y) - ownDy * (b.x - a.x)) /
            (ownLength * projected.length);
          if (!nearEndpoint && projected.distance > 0.25 && sine < MIN_T_ANGLE_SINE) continue;
          const candidate = { node, edge, projected };
          if (!best || projected.distance < best.projected.distance ||
            (projected.distance === best.projected.distance && edge.id < best.edge.id)) best = candidate;
        }
      }
    }
    if (best) candidates.push(best);
  }

  const splits = new Map();
  const connectors = [];
  const inferredJunctions = [];
  for (const { node, edge, projected } of candidates) {
    const a = nodes.get(edge.from);
    const b = nodes.get(edge.to);
    let anchor;
    if (projected.t * projected.length <= 0.15) anchor = a;
    else if ((1 - projected.t) * projected.length <= 0.15) anchor = b;
    else {
      const x = Number(projected.x.toFixed(1));
      const y = Number(projected.y.toFixed(1));
      const id = pointId(x, y);
      anchor = nodes.get(id) ?? { id, x, y };
      nodes.set(id, anchor);
      if (!splits.has(edge.id)) splits.set(edge.id, []);
      splits.get(edge.id).push({ t: projected.t, anchor });
    }
    const connectorId = anchor.id === node.id ? null : `j:${node.id}:${edge.id}`;
    if (connectorId) {
      connectors.push(edgeOf(node, anchor, {
        kind: "inferredJunction",
        sourceWayId: null,
        verified: false,
        targetEdgeId: edge.id,
      }, connectorId));
    }
    inferredJunctions.push({
      nodeId: node.id,
      targetEdgeId: edge.id,
      anchorNodeId: anchor.id,
      gapMeters: Number(projected.distance.toFixed(2)),
      connectorEdgeId: connectorId,
      rule: "endpoint-near-other-component",
      verified: false,
    });
  }

  const edges = [];
  for (const edge of graph.edges) {
    const anchors = splits.get(edge.id);
    if (!anchors) { edges.push(edge); continue; }
    const ordered = [nodes.get(edge.from), ...anchors.sort((a, b) => a.t - b.t)
      .map((item) => item.anchor), nodes.get(edge.to)]
      .filter((node, index, array) => index === 0 || node.id !== array[index - 1].id);
    for (let index = 1; index < ordered.length; index += 1) {
      const split = edgeOf(ordered[index - 1], ordered[index], edge, `${edge.id}:s${index - 1}`);
      if (split.length > 0) edges.push(split);
    }
  }
  edges.push(...connectors);
  const nodeList = [...nodes.values()].sort((a, b) => a.id.localeCompare(b.id));
  const edgeKinds = {};
  for (const edge of edges) edgeKinds[edge.kind] = (edgeKinds[edge.kind] ?? 0) + 1;
  const repairedAdjacency = new Map(nodeList.map((node) => [node.id, []]));
  for (const edge of edges) {
    repairedAdjacency.get(edge.from).push(edge);
    repairedAdjacency.get(edge.to).push(edge);
  }
  const repairedComponents = componentsFor({ nodes: nodeList }, repairedAdjacency);
  const componentSizes = new Map();
  for (const id of repairedComponents.values()) {
    componentSizes.set(id, (componentSizes.get(id) ?? 0) + 1);
  }
  return {
    ...graph,
    connectionRule: "preserved-vertices-plus-conservative-endpoint-snaps",
    nodes: nodeList,
    edges,
    inferredJunctions,
    diagnostics: {
      ...graph.diagnostics,
      rawComponentCount: graph.diagnostics.componentCount,
      componentCount: componentSizes.size,
      largestComponentNodes: Math.max(0, ...componentSizes.values()),
      inferredJunctionCount: inferredJunctions.length,
      snapToleranceMeters: snapMeters,
      edgeKinds,
    },
  };
}
