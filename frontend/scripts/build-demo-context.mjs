// Generate a lightweight, source-attributed SVG fallback from OSM geometries.
// Usage: node frontend/scripts/build-demo-context.mjs
// Output is JSON on stdout and must be reviewed before committing.

import { readFileSync } from "node:fs";
import { DISPLAY_PADDING_METERS, expandedLocalBounds, localToWgs } from "../src/mapGeometry.js";

const origin = [121.505, 31.333];
const boundary = JSON.parse(readFileSync(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url)));
const boundaryPoints = boundary.geometry.coordinates[0].map(([lon, lat]) => [
  (lon - origin[0]) * 111320 * Math.cos(origin[1] * Math.PI / 180),
  (origin[1] - lat) * 111320,
]);
function bboxForPadding(padding) {
  const corners = expandedLocalBounds(boundaryPoints, padding).map((point) => localToWgs(point, origin));
  const lons = corners.map(([lon]) => lon);
  const lats = corners.map(([, lat]) => lat);
  return `(${Math.min(...lats).toFixed(6)},${Math.min(...lons).toFixed(6)},${Math.max(...lats).toFixed(6)},${Math.max(...lons).toFixed(6)})`;
}
const bbox = bboxForPadding(DISPLAY_PADDING_METERS);
// Keep buildings near the selection area, while roads and landscape cover the full reachability context.
const buildingBbox = bboxForPadding(450);
const query = `[out:json][timeout:90];(way[highway]${bbox};way[natural=water]${bbox};way[waterway]${bbox};way[leisure=park]${bbox};way[landuse~"^(grass|forest|recreation_ground)$"]${bbox};way[building]${buildingBbox};);out tags geom;`;
const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;

function project(point) {
  return [
    (point.lon - origin[0]) * 111320 * Math.cos(origin[1] * Math.PI / 180),
    (origin[1] - point.lat) * 111320,
  ];
}

function segmentDistance(point, start, end) {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const amount = dx * dx + dy * dy;
  const t = amount ? Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / amount)) : 0;
  return Math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy);
}

function simplify(points, tolerance) {
  if (points.length <= 2) return points;
  let farthest = 0;
  let split = 0;
  for (let index = 1; index < points.length - 1; index += 1) {
    const distance = segmentDistance(points[index], points[0], points.at(-1));
    if (distance > farthest) { farthest = distance; split = index; }
  }
  if (farthest <= tolerance) return [points[0], points.at(-1)];
  return [
    ...simplify(points.slice(0, split + 1), tolerance).slice(0, -1),
    ...simplify(points.slice(split), tolerance),
  ];
}

function classify(tags) {
  if (tags.natural === "water") return "waterArea";
  if (tags.waterway) return "waterLine";
  if (tags.leisure === "park" || ["grass", "forest", "recreation_ground"].includes(tags.landuse)) return "park";
  if (tags.building) return "building";
  if (["trunk", "primary", "secondary", "tertiary", "primary_link", "secondary_link", "tertiary_link"].includes(tags.highway)) return "roadMajor";
  if (["residential", "unclassified", "pedestrian", "service"].includes(tags.highway)) return "roadLocal";
  if (["footway", "path", "cycleway", "steps"].includes(tags.highway)) return "roadPath";
  return null;
}

function svgPath(points, closed) {
  const coordinates = simplify(points, closed ? 1 : 2);
  return coordinates.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("") + (closed ? "Z" : "");
}

const response = await fetch(url, {
  headers: {
    "User-Agent": "15min-LivingCircle map preview https://github.com/Sa4pphire/15min-LivingCircle",
  },
  signal: AbortSignal.timeout(120000),
});
if (!response.ok) throw new Error(`OpenStreetMap Overpass returned ${response.status}`);
const data = await response.json();
const features = [];
const counts = {};
for (const element of data.elements) {
  const kind = classify(element.tags ?? {});
  if (!kind || !element.geometry || element.geometry.length < 2) continue;
  const points = element.geometry.map(project);
  const closed = ["waterArea", "park", "building"].includes(kind) &&
    element.geometry[0].lon === element.geometry.at(-1).lon &&
    element.geometry[0].lat === element.geometry.at(-1).lat;
  if (["waterArea", "park", "building"].includes(kind) && !closed) continue;
  if (kind === "roadPath" && points.every((point) => Math.hypot(point[0] - points[0][0], point[1] - points[0][1]) < 12)) continue;
  const d = svgPath(points, closed);
  if (!d) continue;
  features.push({ id: element.id, kind, d });
  counts[kind] = (counts[kind] ?? 0) + 1;
}
console.error(counts);
console.log(JSON.stringify({
  coordType: "local-meters-from-wgs84",
  originWgs84: origin,
  source: "OpenStreetMap contributors",
  sourceLicense: "ODbL 1.0",
  sourceUrl: "https://www.openstreetmap.org/copyright",
  sourceQuery: query,
  displayPaddingMeters: DISPLAY_PADDING_METERS,
  buildingPaddingMeters: 450,
  features,
}));
