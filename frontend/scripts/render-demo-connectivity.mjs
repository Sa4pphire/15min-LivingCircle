// Focused route-connectivity QA for a reproducible sample origin.
// Usage: node scripts/render-demo-connectivity.mjs --write

import { readFileSync, writeFileSync } from "node:fs";
import { buildDemoAnalysis } from "../src/analysisClient.js";
import { buildDemoRoadGraph } from "../src/roadGraph.js";
import { wgsToLocal } from "../src/mapGeometry.js";

const selectionOnly = process.argv.includes("--selection-only");
const graph = selectionOnly ? (() => {
  const context = JSON.parse(readFileSync(new URL("../src/data/demoContext.extended.wgs84.json", import.meta.url)));
  const boundary = JSON.parse(readFileSync(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url)));
  const rings = boundary.geometry.coordinates.map((ring) => ring.map((point) =>
    wgsToLocal(point, context.originWgs84)));
  return buildDemoRoadGraph(context.features, rings);
})() : JSON.parse(readFileSync(new URL("../src/data/demoRoadGraph.local.json", import.meta.url)));
const [originX, originY] = wgsToLocal([121.501132, 31.333337], [121.505, 31.333]);
const result = buildDemoAnalysis(graph, { x: originX, y: originY });
const width = 1100;
const height = 840;
const scale = 0.34;
const project = ([x, y]) => [width / 2 + (x - originX) * scale, height / 2 + (y - originY) * scale];
const path = ([a, b]) => {
  const p = project(a);
  const q = project(b);
  return `M${p[0].toFixed(1)} ${p[1].toFixed(1)}L${q[0].toFixed(1)} ${q[1].toFixed(1)}`;
};
const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
const degree = new Map(graph.nodes.map((node) => [node.id, 0]));
for (const edge of graph.edges) {
  degree.set(edge.from, degree.get(edge.from) + 1);
  degree.set(edge.to, degree.get(edge.to) + 1);
}
const visible = ([x, y]) => Math.abs(x - originX) <= 1450 && Math.abs(y - originY) <= 1150;
const context = graph.edges.filter((edge) => {
  const a = nodes.get(edge.from);
  const b = nodes.get(edge.to);
  return visible([a.x, a.y]) || visible([b.x, b.y]);
}).map((edge) => {
  const a = nodes.get(edge.from);
  const b = nodes.get(edge.to);
  return `<path d="${path([[a.x, a.y], [b.x, b.y]])}"/>`;
}).join("");
const routes = result.routeSegments.map((segment) => `<path d="${path(segment.points)}"/>`).join("");
const routeNodeIds = new Set(result.routeSegments.flatMap((segment) =>
  segment.points.flatMap(([x, y]) => [`p:${x.toFixed(1)}:${y.toFixed(1)}`])));
const terminals = graph.nodes.filter((node) => degree.get(node.id) === 1 &&
  routeNodeIds.has(node.id) && visible([node.x, node.y]))
  .map((node) => {
    const [x, y] = project([node.x, node.y]);
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3"/>`;
  }).join("");
const [ox, oy] = project([originX, originY]);
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}">
<rect width="${width}" height="${height}" fill="#f2f6f5"/>
<g fill="none" stroke="#bed2ca" stroke-width="1" stroke-linecap="round">${context}</g>
<circle cx="${ox}" cy="${oy}" r="${1170 * scale}" fill="#147d72" fill-opacity=".055" stroke="#147d72" stroke-width="1.5"/>
<g fill="none" stroke="#087e71" stroke-width="3" stroke-linecap="round">${routes}</g>
<g fill="#d1764e">${terminals}</g>
<circle cx="${ox}" cy="${oy}" r="6" fill="#fff" stroke="#147d72" stroke-width="3"/>
<text x="25" y="37" fill="#173c3a" font-size="22" font-family="sans-serif" font-weight="700">选点附近 · ${selectionOnly ? "旧版选区内" : "扩展地图"}路网连接性校核</text>
<text x="25" y="61" fill="#42685d" font-size="13" font-family="sans-serif">青色：当前路线　橙点：路线走到的图端点　灰色：未走通的附近道路</text>
<text x="25" y="${height - 23}" fill="#42685d" font-size="13" font-family="sans-serif">121.501132, 31.333337 · ${result.routeSegments.length} 条路线段 · OSM 衍生预览，非真实步行可达</text>
</svg>`;
if (process.argv.includes("--write")) {
  writeFileSync(new URL(selectionOnly ? "../public/demo-connectivity-before.svg" :
    "../public/demo-connectivity-audit.svg", import.meta.url), svg);
} else console.log(svg);
