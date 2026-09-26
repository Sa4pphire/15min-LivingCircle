import { pointInPolygon } from "./mapGeometry.js";

const ROAD_KINDS = new Set(["roadMajor", "roadLocal", "roadPath"]);
const EPSILON = 1e-8;

export function parseRoadPath(path) {
  const points = [];
  const commands = /([ML])\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)/g;
  for (const match of path.matchAll(commands)) {
    points.push([Number(match[2]), Number(match[3])]);
  }
  if (points.length < 2 || /[^ML\d.\s-]/.test(path)) {
    throw new Error("Road path must contain only straight M/L coordinates");
  }
  return points;
}

function cross([ax, ay], [bx, by]) {
  return ax * by - ay * bx;
}

function intersectionParameters(start, end, a, b) {
  const direction = [end[0] - start[0], end[1] - start[1]];
  const boundaryDirection = [b[0] - a[0], b[1] - a[1]];
  const offset = [a[0] - start[0], a[1] - start[1]];
  const denominator = cross(direction, boundaryDirection);
  if (Math.abs(denominator) < EPSILON) {
    if (Math.abs(cross(offset, direction)) > EPSILON) return [];
    const lengthSquared = direction[0] ** 2 + direction[1] ** 2;
    if (lengthSquared < EPSILON) return [];
    return [a, b].map((point) =>
      ((point[0] - start[0]) * direction[0] + (point[1] - start[1]) * direction[1]) / lengthSquared);
  }
  const t = cross(offset, boundaryDirection) / denominator;
  const u = cross(offset, direction) / denominator;
  return t >= -EPSILON && t <= 1 + EPSILON && u >= -EPSILON && u <= 1 + EPSILON ? [t] : [];
}

export function clipSegmentToPolygon(start, end, rings) {
  const times = [0, 1];
  for (const ring of rings) {
    for (let index = 1; index < ring.length; index += 1) {
      times.push(...intersectionParameters(start, end, ring[index - 1], ring[index]));
    }
  }
  const ordered = [...new Set(times.map((time) => Math.max(0, Math.min(1, time)).toFixed(10)))]
    .map(Number).sort((a, b) => a - b);
  const pointAt = (time) => [
    start[0] + (end[0] - start[0]) * time,
    start[1] + (end[1] - start[1]) * time,
  ];
  const pieces = [];
  for (let index = 1; index < ordered.length; index += 1) {
    const from = ordered[index - 1];
    const to = ordered[index];
    if (to - from < EPSILON || !pointInPolygon(pointAt((from + to) / 2), rings)) continue;
    pieces.push([pointAt(from), pointAt(to)]);
  }
  return pieces;
}

function roundedPoint([x, y]) {
  return [Number(x.toFixed(1)), Number(y.toFixed(1))];
}

function connectivityStats(nodes, edges) {
  const adjacency = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of edges) {
    adjacency.get(edge.from).push(edge.to);
    adjacency.get(edge.to).push(edge.from);
  }
  const visited = new Set();
  const sizes = [];
  for (const node of nodes) {
    if (visited.has(node.id)) continue;
    const queue = [node.id];
    visited.add(node.id);
    for (let index = 0; index < queue.length; index += 1) {
      for (const neighbor of adjacency.get(queue[index])) {
        if (visited.has(neighbor)) continue;
        visited.add(neighbor);
        queue.push(neighbor);
      }
    }
    sizes.push(queue.length);
  }
  return {
    componentCount: sizes.length,
    largestComponentNodes: Math.max(0, ...sizes),
    edgeKinds: Object.fromEntries([...ROAD_KINDS].map((kind) =>
      [kind, edges.filter((edge) => edge.kind === kind).length])),
  };
}

export function buildDemoRoadGraph(features, selectionRings, coverageRings = selectionRings) {
  const nodesById = new Map();
  const sourceWaysByNode = new Map();
  const edges = [];
  const nodeFor = (point, sourceWayId) => {
    const [x, y] = roundedPoint(point);
    const id = `p:${x.toFixed(1)}:${y.toFixed(1)}`;
    if (!nodesById.has(id)) nodesById.set(id, { id, x, y });
    if (!sourceWaysByNode.has(id)) sourceWaysByNode.set(id, new Set());
    sourceWaysByNode.get(id).add(sourceWayId);
    return id;
  };

  for (const feature of features.filter((item) => ROAD_KINDS.has(item.kind))
    .sort((a, b) => Number(a.id) - Number(b.id))) {
    const points = parseRoadPath(feature.d);
    for (let index = 1; index < points.length; index += 1) {
      const clipped = clipSegmentToPolygon(points[index - 1], points[index], coverageRings);
      for (let part = 0; part < clipped.length; part += 1) {
        const [start, end] = clipped[part];
        const from = nodeFor(start, feature.id);
        const to = nodeFor(end, feature.id);
        if (from === to) continue;
        const a = nodesById.get(from);
        const b = nodesById.get(to);
        edges.push({
          id: `w:${feature.id}:${index - 1}:${part}`,
          from,
          to,
          kind: feature.kind,
          sourceWayId: feature.id,
          length: Number(Math.hypot(b.x - a.x, b.y - a.y).toFixed(1)),
        });
      }
    }
  }

  const nodes = [...nodesById.values()].sort((a, b) => a.id.localeCompare(b.id));
  const junctions = [...sourceWaysByNode]
    .filter(([, ways]) => ways.size > 1)
    .map(([nodeId, ways]) => ({ nodeId, sourceWayIds: [...ways].sort((a, b) => Number(a) - Number(b)) }))
    .sort((a, b) => a.nodeId.localeCompare(b.nodeId));
  return {
    schemaVersion: 1,
    kind: "synthetic-road-graph",
    coordinateSystem: "preview-local-v1",
    connectionRule: "coincident-preserved-vertices-only",
    selectionBoundary: selectionRings.map((ring) => ring.map(roundedPoint)),
    coverageBoundary: coverageRings.map((ring) => ring.map(roundedPoint)),
    nodes,
    edges,
    junctions,
    diagnostics: connectivityStats(nodes, edges),
    source: {
      geometry: "OpenStreetMap-derived SVG preview paths",
      license: "ODbL 1.0",
      url: "https://www.openstreetmap.org/copyright",
    },
  };
}
