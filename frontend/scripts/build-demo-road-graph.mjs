// Generate a synthetic route-animation graph from the checked-in SVG preview.
// Usage from frontend/: node scripts/build-demo-road-graph.mjs --write
// The graph has only local SVG-aligned coordinates; it is not a verified walking network.

import { readFileSync, writeFileSync } from "node:fs";
import { buildDemoRoadGraph } from "../src/roadGraph.js";
import { repairDemoRoadGraph } from "../src/roadGraphRepair.js";
import { expandedLocalBounds, wgsToLocal } from "../src/mapGeometry.js";

const context = JSON.parse(readFileSync(new URL("../src/data/demoContext.extended.wgs84.json", import.meta.url)));
const boundary = JSON.parse(readFileSync(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url)));
const origin = context.originWgs84;
const rings = boundary.geometry.coordinates.map((ring) =>
  ring.map((point) => wgsToLocal(point, origin)));
const corners = expandedLocalBounds(rings[0]);
const coverageRings = [[...corners, corners[0]]];
const graph = repairDemoRoadGraph(buildDemoRoadGraph(context.features, rings, coverageRings));
console.error(`Road graph: ${graph.nodes.length} nodes, ${graph.edges.length} edges, ${graph.junctions.length} shared vertices, ${graph.inferredJunctions.length} inferred links, ${graph.diagnostics.componentCount} components`);
if (process.argv.includes("--write")) {
  writeFileSync(new URL("../src/data/demoRoadGraph.local.json", import.meta.url), JSON.stringify(graph));
} else {
  console.log(JSON.stringify(graph));
}
