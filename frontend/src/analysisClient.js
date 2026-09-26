// Stable display contract for the real-area map. Replace requestMapAnalysis with
// a Python API adapter later; the map only consumes this normalized result.
export const DEMO_RADIUS_UNITS = 1170;

class MinHeap {
  items = [];

  push(item) {
    const items = this.items;
    let index = items.length;
    items.push(item);
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (items[parent][0] <= item[0]) break;
      items[index] = items[parent];
      index = parent;
    }
    items[index] = item;
  }

  pop() {
    const items = this.items;
    const first = items[0];
    const last = items.pop();
    if (items.length) {
      let index = 0;
      while (index * 2 + 1 < items.length) {
        let child = index * 2 + 1;
        if (child + 1 < items.length && items[child + 1][0] < items[child][0]) child += 1;
        if (items[child][0] >= last[0]) break;
        items[index] = items[child];
        index = child;
      }
      items[index] = last;
    }
    return first;
  }

  get size() { return this.items.length; }
}

function nearestEdgeTo(point, graph, nodes) {
  let nearest = null;
  for (const edge of graph.edges) {
    const a = nodes.get(edge.from);
    const b = nodes.get(edge.to);
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) continue;
    const fraction = Math.max(0, Math.min(1,
      ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared));
    const projection = [a.x + dx * fraction, a.y + dy * fraction];
    const distance = Math.hypot(point.x - projection[0], point.y - projection[1]);
    if (!nearest || distance < nearest.distance) nearest = { edge, projection, fraction, distance };
  }
  return nearest;
}

function makeSegment(id, from, to, startDistance, radius) {
  const fullLength = Math.hypot(to[0] - from[0], to[1] - from[1]);
  const length = Math.min(fullLength, radius - startDistance);
  if (length < 0.5 || fullLength < 0.5) return null;
  const fraction = length / fullLength;
  return {
    id,
    points: [from, [from[0] + (to[0] - from[0]) * fraction,
      from[1] + (to[1] - from[1]) * fraction]],
    startDistance: Number(startDistance.toFixed(2)),
    length: Number(length.toFixed(2)),
  };
}

export function buildDemoAnalysis(graph, origin, { radius = DEMO_RADIUS_UNITS } = {}) {
  if (graph.coordinateSystem !== "preview-local-v1") throw new Error("UNSUPPORTED_COORDINATE_SYSTEM");
  if (![origin?.x, origin?.y, radius].every(Number.isFinite) || radius <= 0) {
    throw new Error("INVALID_ANALYSIS_INPUT");
  }
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const nearest = nearestEdgeTo(origin, graph, nodes);
  if (!nearest) throw new Error("EMPTY_ROAD_GRAPH");

  const adjacency = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) {
    adjacency.get(edge.from).push({ to: edge.to, length: edge.length });
    adjacency.get(edge.to).push({ to: edge.from, length: edge.length });
  }
  const distances = new Map();
  const heap = new MinHeap();
  const seed = (nodeId, distance) => {
    if (distance >= (distances.get(nodeId) ?? Infinity)) return;
    distances.set(nodeId, distance);
    heap.push([distance, nodeId]);
  };
  const source = nearest.edge;
  seed(source.from, nearest.distance + nearest.fraction * source.length);
  seed(source.to, nearest.distance + (1 - nearest.fraction) * source.length);
  while (heap.size) {
    const [distance, nodeId] = heap.pop();
    if (distance !== distances.get(nodeId)) continue;
    if (distance > radius) break;
    for (const link of adjacency.get(nodeId)) seed(link.to, distance + link.length);
  }

  const routeSegments = [];
  const from = nodes.get(source.from);
  const to = nodes.get(source.to);
  for (const [suffix, endpoint] of [["from", from], ["to", to]]) {
    const segment = makeSegment(`${source.id}:${suffix}`, nearest.projection,
      [endpoint.x, endpoint.y], nearest.distance, radius);
    if (segment) routeSegments.push(segment);
  }
  for (const edge of graph.edges) {
    if (edge.id === source.id) continue;
    const fromDistance = distances.get(edge.from) ?? Infinity;
    const toDistance = distances.get(edge.to) ?? Infinity;
    const startId = fromDistance <= toDistance ? edge.from : edge.to;
    const endId = startId === edge.from ? edge.to : edge.from;
    const startDistance = Math.min(fromDistance, toDistance);
    if (startDistance >= radius) continue;
    const start = nodes.get(startId);
    const end = nodes.get(endId);
    const segment = makeSegment(edge.id, [start.x, start.y], [end.x, end.y], startDistance, radius);
    if (segment) routeSegments.push(segment);
  }
  routeSegments.sort((a, b) => a.startDistance - b.startDistance || a.id.localeCompare(b.id));

  return {
    schemaVersion: 1,
    source: "synthetic-road-model",
    coordinateSystem: "preview-local-v1",
    origin: { x: origin.x, y: origin.y },
    displayArea: { type: "circle", center: { x: origin.x, y: origin.y }, radius },
    accessLink: {
      points: [[origin.x, origin.y], nearest.projection],
      length: Number(nearest.distance.toFixed(2)),
      verified: false,
    },
    routeSegments,
    summary: {
      routeSegmentCount: routeSegments.length,
      snappedRoadEdgeId: source.id,
      accessDistance: Number(nearest.distance.toFixed(2)),
    },
  };
}

export async function requestMapAnalysis({ origin }, { signal } = {}) {
  // This is the real-area SVG preview, intentionally independent of the C++ demo.
  const { default: demoGraph } = await import("./demoGraphLoader.js");
  if (signal?.aborted) throw new DOMException("Analysis cancelled", "AbortError");
  return buildDemoAnalysis(demoGraph, origin);
}
