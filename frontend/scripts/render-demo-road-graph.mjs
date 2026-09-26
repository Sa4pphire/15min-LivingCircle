// Usage from frontend/: node scripts/render-demo-road-graph.mjs --write
// Static QA view; the graph JSON is the source of truth for future animation.

import { readFileSync, writeFileSync } from "node:fs";
import { fitLocalPoints } from "../src/mapGeometry.js";

const graph = JSON.parse(readFileSync(new URL("../src/data/demoRoadGraph.local.json", import.meta.url)));
const width = 1200;
const height = 820;
const boundary = graph.selectionBoundary[0];
const coverage = graph.coverageBoundary[0];
const fit = fitLocalPoints(coverage, width, height - 90, 0.07);
const project = ([x, y]) => [fit.translateX + x * fit.scale, fit.translateY + y * fit.scale + 65];
const pathFor = (points, close = false) => points.map((point, index) => {
  const [x, y] = project(point);
  return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
}).join(" ") + (close ? " Z" : "");
const nodes = new Map(graph.nodes.map((node) => [node.id, [node.x, node.y]]));
const colors = { roadMajor: "#b8cfc4", roadLocal: "#367f6f", roadPath: "#d1764e", inferredJunction: "#a65383" };
const strokes = { roadMajor: 2.6, roadLocal: 2.2, roadPath: 1.25, inferredJunction: 1.6 };
const roads = graph.edges.map((edge) =>
  `<path d="${pathFor([nodes.get(edge.from), nodes.get(edge.to)])}" stroke="${colors[edge.kind]}" stroke-width="${strokes[edge.kind]}"/>`).join("\n");
const junctions = graph.junctions.map(({ nodeId }) => {
  const [x, y] = project(nodes.get(nodeId));
  return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.5" fill="#173c3a"/>`;
}).join("\n");
const outline = pathFor(boundary, true);
const coverageOutline = pathFor(coverage, true);

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="完整展示范围的合成道路图校核预览">
<rect width="${width}" height="${height}" fill="#f2f6f5"/>
<path d="${outline}" fill="#e5f2e9" stroke="none"/>
<g fill="none" stroke-linecap="round" stroke-linejoin="round">${roads}</g>
<g>${junctions}</g>
<path d="${coverageOutline}" fill="none" stroke="#a4c5b8" stroke-width="1.6"/>
<path d="${outline}" fill="none" stroke="#147d72" stroke-width="2" stroke-dasharray="9 6"/>
<text x="40" y="38" fill="#173c3a" font-family="sans-serif" font-size="21" font-weight="700">完整地图展示范围 · 合成道路图</text>
<text x="40" y="60" fill="#42685d" font-family="sans-serif" font-size="12">绿色虚线为四路围合起点选区 · 浅色外框为路网展示范围 · 紫线为未核实的端点贴近补连</text>
<text x="40" y="${height - 28}" fill="#42685d" font-family="sans-serif" font-size="14">主干道  •  支路  •  步行与慢行路径　｜　${graph.nodes.length} 节点 / ${graph.edges.length} 道路边 / ${graph.diagnostics.componentCount} 连通片</text>
<text x="${width - 40}" y="38" text-anchor="end" fill="#53766b" font-family="sans-serif" font-size="12">© OpenStreetMap contributors · ODbL · 步行权限未核实</text>
</svg>`;
if (process.argv.includes("--write")) {
  writeFileSync(new URL("../public/demo-road-graph.svg", import.meta.url), svg);
} else {
  console.log(svg);
}
