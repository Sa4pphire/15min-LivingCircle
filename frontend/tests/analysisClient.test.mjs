import test from "node:test";
import assert from "node:assert/strict";
import { buildDemoAnalysis, DEMO_RADIUS_UNITS, requestMapAnalysis } from "../src/analysisClient.js";

const graph = {
  coordinateSystem: "preview-local-v1",
  nodes: [
    { id: "a", x: 0, y: 0 }, { id: "b", x: 100, y: 0 },
    { id: "c", x: 200, y: 0 }, { id: "d", x: 1000, y: 0 }, { id: "e", x: 1100, y: 0 },
  ],
  edges: [
    { id: "ab", from: "a", to: "b", length: 100 },
    { id: "bc", from: "b", to: "c", length: 100 },
    { id: "de", from: "d", to: "e", length: 100 },
  ],
};

test("analysis keeps a fixed circle but clips connected route segments by graph distance", () => {
  const result = buildDemoAnalysis(graph, { x: 40, y: 20 }, { radius: 120 });
  assert.equal(result.coordinateSystem, "preview-local-v1");
  assert.deepEqual(result.displayArea, { type: "circle", center: { x: 40, y: 20 }, radius: 120 });
  assert.deepEqual(result.accessLink.points, [[40, 20], [40, 0]]);
  assert.equal(result.accessLink.verified, false);
  assert.equal(result.routeSegments.some((segment) => segment.id === "de"), false);
  const continuation = result.routeSegments.find((segment) => segment.id === "bc");
  assert.deepEqual(continuation.points, [[100, 0], [140, 0]]);
  assert.ok(result.routeSegments.every((segment) => segment.startDistance + segment.length <= 120.01));
});

test("selecting another point changes the route origin without changing the fixed radius", () => {
  const first = buildDemoAnalysis(graph, { x: 40, y: 20 });
  const second = buildDemoAnalysis(graph, { x: 150, y: 0 });
  assert.equal(first.displayArea.radius, DEMO_RADIUS_UNITS);
  assert.equal(second.displayArea.radius, DEMO_RADIUS_UNITS);
  assert.notDeepEqual(first.origin, second.origin);
  assert.notEqual(first.summary.snappedRoadEdgeId, second.summary.snappedRoadEdgeId);
});

test("analysis rejects invalid input and an empty graph", () => {
  assert.throws(() => buildDemoAnalysis(graph, { x: NaN, y: 0 }), /INVALID_ANALYSIS_INPUT/);
  assert.throws(() => buildDemoAnalysis({ ...graph, edges: [] }, { x: 0, y: 0 }), /EMPTY_ROAD_GRAPH/);
});

test("lazy adapter returns the normalized contract for the checked-in demo graph", async () => {
  const result = await requestMapAnalysis({ origin: { x: 0, y: 0 } });
  assert.equal(result.source, "synthetic-road-model");
  assert.equal(result.displayArea.radius, DEMO_RADIUS_UNITS);
  assert.ok(result.routeSegments.length > 20);
});
