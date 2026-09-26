import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildDemoAnalysis } from "../src/analysisClient.js";
import { buildDemoRoadGraph, clipSegmentToPolygon, parseRoadPath } from "../src/roadGraph.js";
import { repairDemoRoadGraph } from "../src/roadGraphRepair.js";
import { expandedLocalBounds, pointInPolygon, wgsToLocal } from "../src/mapGeometry.js";

const square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];

function graphComponents(graph) {
  const adjacent = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) {
    adjacent.get(edge.from).push(edge.to);
    adjacent.get(edge.to).push(edge.from);
  }
  const componentByNode = new Map();
  const components = [];
  for (const node of graph.nodes) {
    if (componentByNode.has(node.id)) continue;
    const queue = [node.id];
    const index = components.length;
    componentByNode.set(node.id, index);
    for (let head = 0; head < queue.length; head += 1) {
      for (const next of adjacent.get(queue[head])) {
        if (componentByNode.has(next)) continue;
        componentByNode.set(next, index);
        queue.push(next);
      }
    }
    components.push(queue);
  }
  return { adjacent, componentByNode, components };
}

test("road path parser preserves local map coordinates", () => {
  assert.deepEqual(parseRoadPath("M-1.2 3.4L5 6L7.1 -2"), [[-1.2, 3.4], [5, 6], [7.1, -2]]);
  assert.throws(() => parseRoadPath("M0 0 Q5 5 10 10"));
});

test("road segments are clipped to selection boundary, including two crossings", () => {
  assert.deepEqual(clipSegmentToPolygon([-5, 5], [15, 5], [square]), [[[0, 5], [10, 5]]]);
  assert.deepEqual(clipSegmentToPolygon([-5, -5], [-1, -1], [square]), []);
  assert.deepEqual(clipSegmentToPolygon([2, 2], [8, 8], [square]), [[[2, 2], [8, 8]]]);
  const hole = [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]];
  assert.deepEqual(clipSegmentToPolygon([-5, 5], [15, 5], [square, hole]), [
    [[0, 5], [3, 5]], [[7, 5], [10, 5]],
  ]);
});

test("geometric crossings without a retained common vertex do not connect", () => {
  const graph = buildDemoRoadGraph([
    { id: 1, kind: "roadLocal", d: "M1 5L9 5" },
    { id: 2, kind: "roadPath", d: "M5 1L5 9" },
  ], [square]);
  assert.equal(graph.edges.length, 2);
  assert.equal(graph.nodes.length, 4);
  assert.equal(graph.junctions.length, 0);
});

test("shared source vertices form a provisional synthetic junction", () => {
  const graph = buildDemoRoadGraph([
    { id: 1, kind: "roadLocal", d: "M1 5L5 5L9 5" },
    { id: 2, kind: "roadPath", d: "M5 1L5 5L5 9" },
  ], [square]);
  assert.equal(graph.edges.length, 4);
  assert.equal(graph.nodes.length, 5);
  assert.deepEqual(graph.junctions, [{ nodeId: "p:5.0:5.0", sourceWayIds: [1, 2] }]);
});

test("coverage can expand without changing the permitted origin polygon", () => {
  const coverage = [[-5, -5], [15, -5], [15, 15], [-5, 15], [-5, -5]];
  const graph = buildDemoRoadGraph([
    { id: 1, kind: "roadLocal", d: "M-10 5L5 5L20 5" },
  ], [square], [coverage]);
  assert.deepEqual(graph.selectionBoundary, [square]);
  assert.deepEqual(graph.coverageBoundary, [coverage]);
  assert.ok(graph.nodes.some((node) => node.x < 0));
  assert.ok(graph.nodes.some((node) => node.x > 10));
  assert.ok(graph.nodes.every((node) => node.x >= -5 && node.x <= 15));
});

test("a near perpendicular endpoint joins another component by splitting its road edge", () => {
  const raw = buildDemoRoadGraph([
    { id: 1, kind: "roadLocal", d: "M2 8L9 8" },
    { id: 2, kind: "roadLocal", d: "M10 3L10 13" },
  ], [square]);
  const repaired = repairDemoRoadGraph(raw);
  assert.equal(raw.diagnostics.componentCount, 2);
  assert.equal(repaired.diagnostics.componentCount, 1);
  assert.equal(repaired.inferredJunctions.length, 1);
  assert.ok(repaired.edges.some((edge) => edge.kind === "inferredJunction" &&
    edge.length === 1 && edge.verified === false));
  assert.equal(repaired.edges.filter((edge) => edge.sourceWayId === 2).length, 2);
  assert.equal(buildDemoAnalysis(raw, { x: 2, y: 8 }, { radius: 30 }).routeSegments
    .some((segment) => segment.id.startsWith("w:2:")), false);
  assert.equal(buildDemoAnalysis(repaired, { x: 2, y: 8 }, { radius: 30 }).routeSegments
    .some((segment) => segment.id.startsWith("w:2:")), true);
});

test("parallel close roads and interior-only crossings do not create invented junctions", () => {
  const parallel = buildDemoRoadGraph([
    { id: 1, kind: "roadLocal", d: "M3 5L7 5" },
    { id: 2, kind: "roadLocal", d: "M0 6L10 6" },
  ], [square]);
  const crossing = buildDemoRoadGraph([
    { id: 3, kind: "roadLocal", d: "M1 5L9 5" },
    { id: 4, kind: "roadPath", d: "M5 1L5 9" },
  ], [square]);
  assert.equal(repairDemoRoadGraph(parallel).inferredJunctions.length, 0);
  assert.equal(repairDemoRoadGraph(crossing).inferredJunctions.length, 0);
  assert.throws(() => repairDemoRoadGraph(crossing, { snapMeters: 5 }), /INVALID_SNAP_TOLERANCE/);
});

test("the entire generated graph is reproducible from the saved map geometry", () => {
  const saved = JSON.parse(readFileSync(new URL("../src/data/demoRoadGraph.local.json", import.meta.url)));
  const context = JSON.parse(readFileSync(new URL("../src/data/demoContext.extended.wgs84.json", import.meta.url)));
  const boundary = JSON.parse(readFileSync(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url)));
  const rings = boundary.geometry.coordinates.map((ring) =>
    ring.map((point) => wgsToLocal(point, context.originWgs84)));
  const corners = expandedLocalBounds(rings[0]);
  const rebuilt = repairDemoRoadGraph(buildDemoRoadGraph(context.features, rings,
    [[...corners, corners[0]]]));
  // JSON serialization normalizes negative zero in local coordinates.
  assert.equal(JSON.stringify(rebuilt), JSON.stringify(saved));
});

test("checked-in graph covers the map extent but keeps origins inside four roads", () => {
  const graph = JSON.parse(readFileSync(new URL("../src/data/demoRoadGraph.local.json", import.meta.url)));
  const boundary = JSON.parse(readFileSync(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url)));
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const edgeIds = new Set(graph.edges.map((edge) => edge.id));
  assert.equal(graph.coordinateSystem, "preview-local-v1");
  assert.equal(graph.connectionRule, "preserved-vertices-plus-conservative-endpoint-snaps");
  assert.ok(graph.nodes.length > 2203);
  assert.ok(graph.edges.length > 2038);
  assert.ok(graph.junctions.length > 10);
  assert.ok(graph.diagnostics.componentCount > 1);
  assert.ok(graph.diagnostics.componentCount < graph.diagnostics.rawComponentCount);
  assert.ok(graph.inferredJunctions.length > 100);
  assert.ok(graph.inferredJunctions.every((junction) =>
    junction.gapMeters <= graph.diagnostics.snapToleranceMeters && junction.verified === false));
  assert.equal(nodes.size, graph.nodes.length, "node IDs must be unique");
  assert.equal(edgeIds.size, graph.edges.length, "edge IDs must be unique");
  assert.equal(graph.diagnostics.inferredJunctionCount, graph.inferredJunctions.length);
  for (const junction of graph.inferredJunctions) {
    assert.ok(nodes.has(junction.nodeId) && nodes.has(junction.anchorNodeId));
    if (junction.connectorEdgeId) assert.ok(edgeIds.has(junction.connectorEdgeId));
  }
  assert.ok(graph.diagnostics.largestComponentNodes > 100);
  const expectedCorners = expandedLocalBounds(boundary.geometry.coordinates[0]
    .map((vertex) => wgsToLocal(vertex, [121.505, 31.333])));
  for (let index = 0; index < 4; index += 1) {
    assert.ok(Math.hypot(
      graph.coverageBoundary[0][index][0] - expectedCorners[index][0],
      graph.coverageBoundary[0][index][1] - expectedCorners[index][1],
    ) <= 0.08);
  }
  assert.deepEqual(graph.coverageBoundary[0][0], graph.coverageBoundary[0].at(-1));
  const boundaryDistance = ([x, y], ring) => {
    let closest = Infinity;
    for (let index = 1; index < ring.length; index += 1) {
      const [ax, ay] = ring[index - 1];
      const [bx, by] = ring[index];
      const dx = bx - ax;
      const dy = by - ay;
      const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)));
      closest = Math.min(closest, Math.hypot(x - ax - t * dx, y - ay - t * dy));
    }
    return closest;
  };
  let outsideSelection = 0;
  for (const edge of graph.edges) {
    const a = nodes.get(edge.from);
    const b = nodes.get(edge.to);
    assert.ok(a && b);
    assert.ok(edge.length > 0);
    assert.ok(Math.abs(edge.length - Math.hypot(b.x - a.x, b.y - a.y)) <= 0.06,
      `${edge.id} has inconsistent length`);
    if (edge.kind === "inferredJunction") {
      assert.equal(edge.verified, false);
      assert.ok(edge.length <= graph.diagnostics.snapToleranceMeters + 0.1);
    }
    for (const fraction of [0, 0.5, 1]) {
      const point = [a.x + (b.x - a.x) * fraction, a.y + (b.y - a.y) * fraction];
      assert.ok(pointInPolygon(point, graph.coverageBoundary) ||
        boundaryDistance(point, graph.coverageBoundary[0]) <= 0.2,
      `${edge.id} is outside the display coverage`);
      if (!pointInPolygon(point, graph.selectionBoundary) &&
        boundaryDistance(point, graph.selectionBoundary[0]) > 0.2) outsideSelection += 1;
    }
  }
  assert.ok(outsideSelection > 100, "expanded graph must include substantial roads beyond origin selection");
  const { adjacent, componentByNode, components } = graphComponents(graph);
  assert.ok([...adjacent.values()].every((neighbors) => neighbors.length > 0),
    "no isolated/orphan graph nodes may be serialized");
  assert.equal(components.length, graph.diagnostics.componentCount,
    "component count must agree with the actual edge topology");
  assert.equal(Math.max(...components.map((component) => component.length)),
    graph.diagnostics.largestComponentNodes,
    "largest component must agree with the actual edge topology");
  for (const junction of graph.inferredJunctions) {
    assert.equal(componentByNode.get(junction.nodeId), componentByNode.get(junction.anchorNodeId),
      `${junction.nodeId} did not connect to its inferred anchor`);
  }
  const [x, y] = wgsToLocal([121.501132, 31.333337], [121.505, 31.333]);
  const result = buildDemoAnalysis(graph, { x, y });
  assert.ok(result.routeSegments.length > 400, "sample origin should route through repaired junctions");
  assert.ok(result.routeSegments.some((segment) => segment.id.startsWith("j:")));
  const knownRouteIds = new Set(graph.edges.map((edge) => edge.id));
  for (const segment of result.routeSegments) {
    assert.ok(knownRouteIds.has(segment.id) ||
      [":from", ":to"].some((suffix) =>
        segment.id.endsWith(suffix) && knownRouteIds.has(segment.id.slice(0, -suffix.length))),
    `route ${segment.id} must refer to a connected graph edge`);
  }
});
