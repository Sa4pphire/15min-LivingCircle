// Audit the provisional graph without changing its connectivity.
// Usage: node scripts/audit-demo-road-graph.mjs [originLng originLat]

import { readFileSync } from "node:fs";
import { wgsToLocal } from "../src/mapGeometry.js";

const graph = JSON.parse(readFileSync(new URL("../src/data/demoRoadGraph.local.json", import.meta.url)));
const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
const adjacency = new Map(graph.nodes.map((node) => [node.id, []]));
for (const edge of graph.edges) {
  adjacency.get(edge.from).push(edge);
  adjacency.get(edge.to).push(edge);
}

const componentByNode = new Map();
const components = [];
for (const node of graph.nodes) {
  if (componentByNode.has(node.id)) continue;
  const id = components.length;
  const queue = [node.id];
  componentByNode.set(node.id, id);
  for (let index = 0; index < queue.length; index += 1) {
    for (const edge of adjacency.get(queue[index])) {
      const next = edge.from === queue[index] ? edge.to : edge.from;
      if (!componentByNode.has(next)) {
        componentByNode.set(next, id);
        queue.push(next);
      }
    }
  }
  components.push(queue);
}

const cellSize = 25;
const edgeCells = new Map();
const key = (x, y) => `${x}:${y}`;
const cell = (value) => Math.floor(value / cellSize);
for (const edge of graph.edges) {
  const a = nodes.get(edge.from);
  const b = nodes.get(edge.to);
  for (let x = cell(Math.min(a.x, b.x) - 15); x <= cell(Math.max(a.x, b.x) + 15); x += 1) {
    for (let y = cell(Math.min(a.y, b.y) - 15); y <= cell(Math.max(a.y, b.y) + 15); y += 1) {
      const bucket = key(x, y);
      if (!edgeCells.has(bucket)) edgeCells.set(bucket, []);
      edgeCells.get(bucket).push(edge);
    }
  }
}

function nearestOtherEdge(node) {
  const seen = new Set();
  const candidates = [];
  for (let x = cell(node.x - 15); x <= cell(node.x + 15); x += 1) {
    for (let y = cell(node.y - 15); y <= cell(node.y + 15); y += 1) {
      for (const edge of edgeCells.get(key(x, y)) ?? []) {
        if (seen.has(edge.id) || edge.from === node.id || edge.to === node.id) continue;
        seen.add(edge.id);
        const a = nodes.get(edge.from);
        const b = nodes.get(edge.to);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const t = Math.max(0, Math.min(1,
          ((node.x - a.x) * dx + (node.y - a.y) * dy) / (dx * dx + dy * dy)));
        const point = [a.x + t * dx, a.y + t * dy];
        const distance = Math.hypot(node.x - point[0], node.y - point[1]);
        if (distance <= 15) candidates.push({
          edgeId: edge.id,
          sourceWayId: edge.sourceWayId,
          distance: Number(distance.toFixed(2)),
          t: Number(t.toFixed(3)),
          point,
          sameComponent: componentByNode.get(node.id) === componentByNode.get(edge.from),
        });
      }
    }
  }
  return candidates.sort((a, b) => a.distance - b.distance);
}

const originWgs = process.argv.length >= 4
  ? [Number(process.argv[2]), Number(process.argv[3])] : [121.501132, 31.333337];
const [originX, originY] = wgsToLocal(originWgs, [121.505, 31.333]);
let seed = null;
for (const node of graph.nodes) {
  const distance = Math.hypot(node.x - originX, node.y - originY);
  if (!seed || distance < seed.distance) seed = { node, distance };
}
const originComponent = componentByNode.get(seed.node.id);
const terminals = graph.nodes.filter((node) => adjacency.get(node.id).length === 1);
const nearbyTerminals = terminals.filter((node) =>
  componentByNode.get(node.id) === originComponent &&
  Math.hypot(node.x - originX, node.y - originY) <= 1300)
  .map((node) => ({
    id: node.id,
    x: node.x,
    y: node.y,
    originDistance: Number(Math.hypot(node.x - originX, node.y - originY).toFixed(1)),
    wayId: adjacency.get(node.id)[0].sourceWayId,
    near: nearestOtherEdge(node).slice(0, 5),
  })).sort((a, b) => a.originDistance - b.originDistance);
const globalNearMisses = terminals.map((node) => ({
  node, near: nearestOtherEdge(node).find((candidate) => !candidate.sameComponent),
})).filter((item) => item.near);

console.log(JSON.stringify({
  originWgs,
  originLocal: [Number(originX.toFixed(1)), Number(originY.toFixed(1))],
  graph: {
    nodes: graph.nodes.length,
    edges: graph.edges.length,
    components: components.length,
    terminalNodes: terminals.length,
    originComponentNodes: components[originComponent].length,
    inferredJunctions: graph.inferredJunctions?.length ?? 0,
    edgeKinds: graph.diagnostics.edgeKinds,
  },
  nearMissesAcrossComponents: {
    within0_5m: globalNearMisses.filter((item) => item.near.distance <= 0.5).length,
    within1m: globalNearMisses.filter((item) => item.near.distance <= 1).length,
    within2m: globalNearMisses.filter((item) => item.near.distance <= 2).length,
    within5m: globalNearMisses.filter((item) => item.near.distance <= 5).length,
    within15m: globalNearMisses.length,
  },
  nearbyTerminals: process.argv.includes("--details") ? nearbyTerminals : nearbyTerminals
    .filter((item) => item.near.some((candidate) => !candidate.sameComponent && candidate.distance <= 5))
    .slice(0, 15).map((item) => ({
      id: item.id, x: item.x, y: item.y,
      nearestOtherComponent: item.near.find((candidate) => !candidate.sameComponent),
    })),
}, null, 2));
