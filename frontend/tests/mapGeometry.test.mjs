import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  DISPLAY_PADDING_METERS,
  expandedLocalBounds,
  fitLocalPoints,
  geometryToSvgPath,
  localToWgs,
  pointInPolygon,
  wgsToLocal,
} from "../src/mapGeometry.js";

const boundaryPath = fileURLToPath(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url));
const boundary = JSON.parse(readFileSync(boundaryPath, "utf8"));
const contextPath = fileURLToPath(new URL("../src/data/demoContext.extended.wgs84.json", import.meta.url));
const context = JSON.parse(readFileSync(contextPath, "utf8"));
const ring = boundary.geometry.coordinates[0];

test("four-road GeoJSON is closed and contains the demonstration centre", () => {
  assert.equal(boundary.geometry.type, "Polygon");
  assert.equal(boundary.properties.coordType, "wgs84ll");
  assert.ok(ring.length > 100);
  assert.deepEqual(ring[0], ring.at(-1));
  assert.equal(pointInPolygon([121.505, 31.333], boundary.geometry.coordinates), true);
  assert.equal(pointInPolygon([121.48, 31.333], boundary.geometry.coordinates), false);
  assert.equal(pointInPolygon(ring[0], boundary.geometry.coordinates), true);
});

test("polygon hit testing respects holes", () => {
  const outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
  const hole = [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]];
  assert.equal(pointInPolygon([1, 1], [outer, hole]), true);
  assert.equal(pointInPolygon([5, 5], [outer, hole]), false);
  assert.equal(pointInPolygon([11, 5], [outer, hole]), false);
});

test("local metre conversion round-trips and fitted points stay in viewport", () => {
  const origin = [121.505, 31.333];
  const point = [121.51, 31.337];
  const local = wgsToLocal(point, origin);
  const roundTrip = localToWgs(local, origin);
  assert.ok(Math.abs(roundTrip[0] - point[0]) < 1e-10);
  assert.ok(Math.abs(roundTrip[1] - point[1]) < 1e-10);
  const fit = fitLocalPoints(ring.map((vertex) => wgsToLocal(vertex, origin)), 1200, 700);
  for (const vertex of ring) {
    const [x, y] = wgsToLocal(vertex, origin);
    const screenX = fit.translateX + x * fit.scale;
    const screenY = fit.translateY + y * fit.scale;
    assert.ok(screenX > 0 && screenX < 1200);
    assert.ok(screenY > 0 && screenY < 700);
  }
});

test("display extent covers a 15-minute perimeter without changing the selection polygon", () => {
  const origin = [121.505, 31.333];
  const localRing = ring.map((vertex) => wgsToLocal(vertex, origin));
  const corners = expandedLocalBounds(localRing);
  const xs = localRing.map(([x]) => x);
  const ys = localRing.map(([, y]) => y);
  assert.equal(DISPLAY_PADDING_METERS, 1300);
  assert.equal(corners[0][0], Math.min(...xs) - DISPLAY_PADDING_METERS);
  assert.equal(corners[0][1], Math.min(...ys) - DISPLAY_PADDING_METERS);
  assert.equal(corners[2][0], Math.max(...xs) + DISPLAY_PADDING_METERS);
  assert.equal(corners[2][1], Math.max(...ys) + DISPLAY_PADDING_METERS);
  assert.equal(pointInPolygon([121.48, 31.333], boundary.geometry.coordinates), false);
  const fit = fitLocalPoints(corners, 1200, 700, 0.04);
  for (const [x, y] of corners) {
    assert.ok(fit.translateX + x * fit.scale >= 0 && fit.translateX + x * fit.scale <= 1200);
    assert.ok(fit.translateY + y * fit.scale >= 0 && fit.translateY + y * fit.scale <= 700);
  }
});

test("expanded fallback roads cover the display extent and retain attribution", () => {
  assert.equal(context.displayPaddingMeters, DISPLAY_PADDING_METERS);
  assert.equal(context.source, "OpenStreetMap contributors");
  assert.ok(context.features.some((feature) => feature.kind === "roadMajor"));
  const corners = expandedLocalBounds(ring.map((vertex) => wgsToLocal(vertex, [121.505, 31.333])))
    .map((point) => localToWgs(point, [121.505, 31.333]));
  const lons = corners.map(([lon]) => lon);
  const lats = corners.map(([, lat]) => lat);
  const bbox = `(${Math.min(...lats).toFixed(6)},${Math.min(...lons).toFixed(6)},${Math.max(...lats).toFixed(6)},${Math.max(...lons).toFixed(6)})`;
  assert.ok(context.sourceQuery.includes(bbox));
});

test("GeoJSON projection supports polygon holes, multi-rings and walkway lines", () => {
  const project = ([x, y]) => [x * 2, y * 2];
  const polygon = { type: "Polygon", coordinates: [
    [[0, 0], [10, 0], [10, 10], [0, 0]],
    [[2, 2], [3, 2], [2, 2]],
  ] };
  assert.equal(geometryToSvgPath(polygon, project).match(/Z/g)?.length, 2);
  assert.equal(geometryToSvgPath({ type: "LineString", coordinates: [[1, 2], [3, 4]] }, project), "M2.00 4.00 L6.00 8.00");
  assert.equal(geometryToSvgPath({ type: "MultiLineString", coordinates: [[[0, 0], [1, 1]], [[2, 2], [3, 3]]] }, project).match(/M/g)?.length, 2);
  assert.equal(geometryToSvgPath({ type: "MultiPolygon", coordinates: [polygon.coordinates, polygon.coordinates] }, project).match(/Z/g)?.length, 4);
});
