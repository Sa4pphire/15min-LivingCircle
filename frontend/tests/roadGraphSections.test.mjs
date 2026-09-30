import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { attachDividedRoadSections } from "../src/roadGraphSections.js";

const graph = { coordinateSystem: "preview-local-v1", nodes: [], edges: [
  { id: "west", kind: "roadMajor" }, { id: "east", kind: "roadMajor" },
] };
const spec = { schemaVersion: 1, coordinateSystem: "preview-local-v1", sections: [{
  id: "pilot", medianWalkable: false, verificationStatus: "user_marked_unverified", source: "synthetic test",
  boundsMeters: { minX: -20, maxX: 20, minY: -50, maxY: 50 },
  carriageways: [{ edgeId: "west", outerSide: "right" }, { edgeId: "east", outerSide: "right" }],
}] };

test("explicit pilot metadata preserves source IDs and does not invent topology", () => {
  const before = structuredClone(graph);
  const result = attachDividedRoadSections(graph, spec);
  assert.deepEqual(graph, before);
  assert.equal(result.edges, graph.edges);
  assert.equal(result.nodes, graph.nodes);
  result.dividedRoadSections[0].id = "changed-copy";
  assert.equal(spec.sections[0].id, "pilot");
});

for (const change of ["missing_edge", "duplicate_carriageway", "median", "side", "infinite", "verified", "coords"]) {
  test(`invalid divided-road metadata fails closed: ${change}`, () => {
    const invalid = structuredClone(spec), section = invalid.sections[0];
    if (change === "missing_edge") section.carriageways[0].edgeId = "unknown";
    if (change === "duplicate_carriageway") section.carriageways[1].edgeId = "west";
    if (change === "median") section.medianWalkable = true;
    if (change === "side") section.carriageways[0].outerSide = "inner";
    if (change === "infinite") section.boundsMeters.minY = Infinity;
    if (change === "verified") section.verificationStatus = "verified";
    if (change === "coords") invalid.coordinateSystem = "engine-local-meters";
    assert.throws(() => attachDividedRoadSections(graph, invalid), /INVALID_DIVIDED_ROAD/);
  });
}

test("explicit junction closure metadata is retained, while malformed IDs fail closed", () => {
  const withClosure = structuredClone(spec);
  withClosure.sections[0].junctionApproachClosures = [{
    junctionId: "reviewed", portIds: ["south-0", "south-2"],
    connectorIds: ["median-0", "median-1"],
  }];
  assert.deepEqual(attachDividedRoadSections(graph, withClosure).dividedRoadSections,
    withClosure.sections);
  const invalid = structuredClone(withClosure);
  invalid.sections[0].junctionApproachClosures[0].portIds[1] = "south-0";
  assert.throws(() => attachDividedRoadSections(graph, invalid), /INVALID_DIVIDED_ROAD_SECTION/);
});

test("both modes share the checked-in source and only one bounded pilot is annotated", () => {
  const saved = JSON.parse(readFileSync(new URL("../src/data/demoRoadGraph.local.json", import.meta.url)));
  const specification = JSON.parse(readFileSync(new URL("../src/data/demoSidewalkSections.local.json", import.meta.url)));
  assert.deepEqual(saved.dividedRoadSections, specification.sections);
  assert.equal(saved.dividedRoadSections.length, 1);
  assert.equal(saved.nodes.length, 8518);
  assert.equal(saved.edges.length, 9232);
});
