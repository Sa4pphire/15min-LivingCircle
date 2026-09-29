import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createLocalBd09Alignment, preparedMapCenter, preparedMapGeometry, sampleLocalArea, sampleLocalPath } from "../src/mapAsset.js";
import { DISPLAY_PADDING_METERS, expandedLocalBounds, localToWgs, wgsToLocal } from "../src/mapGeometry.js";

const asset = JSON.parse(readFileSync(new URL("../src/data/demoMap.bd09.json", import.meta.url)));
const boundary = JSON.parse(readFileSync(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url)));
const expected = { ringWgs84: boundary.geometry.coordinates[0],
  originWgs84: [121.505, 31.333], paddingMeters: DISPLAY_PADDING_METERS };

test("prepared BD-09 data matches the fixed WGS84 selection and keeps source attribution", () => {
  const { ring, corners } = preparedMapGeometry(asset, expected);
  assert.equal(ring.length, 168);
  assert.deepEqual(ring[0], ring.at(-1));
  assert.equal(corners.length, 4);
  assert.equal(asset.boundary.properties.source, boundary.properties.source);
  assert.equal(asset.conversion.model, 2);
  assert.doesNotMatch(JSON.stringify(asset), /"(?:ak|sn|key)"\s*:/i);
});

test("changed selection, origin or padding requires explicit regeneration", () => {
  for (const changes of [{ paddingMeters: 1500 }, { originWgs84: [121.5, 31.33] },
    { ringWgs84: expected.ringWgs84.slice(1) }]) {
    assert.throws(() => preparedMapGeometry(asset, { ...expected, ...changes }), /不一致/);
  }
});

test("broken overlay is rejected independently of the usable base map centre", () => {
  for (const corrupt of [value => { value.boundary = null; },
    value => { value.boundary.geometry.coordinates[0].pop(); },
    value => { value.displayCornersBd09 = Array(4).fill(value.centerBd09); }]) {
    const value = structuredClone(asset);
    corrupt(value);
    assert.deepEqual(preparedMapCenter(value), asset.centerBd09);
    assert.throws(() => preparedMapGeometry(value, expected), /不完整|无效/);
  }
  assert.equal(preparedMapCenter({ ...asset, centerBd09: [NaN, 31] }), null);
  assert.equal(preparedMapCenter({ ...asset, coordType: "wgs84ll" }), null);
});

test("Python display extent and the frontend's local extent use the same inputs", () => {
  const origin = expected.originWgs84;
  const corners = expandedLocalBounds(expected.ringWgs84.map(point => wgsToLocal(point, origin)))
    .map(point => localToWgs(point, origin));
  const dx = DISPLAY_PADDING_METERS / (111320 * Math.cos(origin[1] * Math.PI / 180));
  const dy = DISPLAY_PADDING_METERS / 111320;
  assert.ok(Math.abs(corners[0][0] - (Math.min(...expected.ringWgs84.map(point => point[0])) - dx)) < 1e-10);
  assert.ok(Math.abs(corners[0][1] - (Math.max(...expected.ringWgs84.map(point => point[1])) + dy)) < 1e-10);
});

const distanceMeters = (a, b) => Math.hypot(
  (a[0] - b[0]) * 111320 * Math.cos(b[1] * Math.PI / 180), (a[1] - b[1]) * 111320);

test("grid mapping matches independent official boundary conversions, unlike the old affine map", () => {
  const { alignment, corners } = preparedMapGeometry(asset, expected);
  const bounds = expandedLocalBounds(expected.ringWgs84.map(point => wgsToLocal(point, expected.originWgs84)));
  let oldMaximum = 0, newMaximum = 0;
  expected.ringWgs84.forEach((point, index) => {
    const local = wgsToLocal(point, expected.originWgs84), official = asset.boundary.geometry.coordinates[0][index];
    const u = (local[0] - bounds[0][0]) / (bounds[1][0] - bounds[0][0]);
    const v = (local[1] - bounds[0][1]) / (bounds[3][1] - bounds[0][1]);
    const old = [0, 1].map(axis => corners[0][axis] + u * (corners[1][axis] - corners[0][axis]) +
      v * (corners[3][axis] - corners[0][axis]));
    oldMaximum = Math.max(oldMaximum, distanceMeters(old, official));
    newMaximum = Math.max(newMaximum, distanceMeters(alignment.toBd09(local), official));
  });
  assert.ok(oldMaximum > 25, `the old mapping reproduces the defect: ${oldMaximum}m`);
  assert.ok(newMaximum < 0.5, `official validation points differ by only ${newMaximum}m`);
  assert.ok(distanceMeters(alignment.toBd09([0, 0]), asset.centerBd09) < 0.01);
});

test("forward drawing and inverse selection share exactly the same grid cells", () => {
  const grid = asset.alignment, alignment = createLocalBd09Alignment(asset);
  const points = [[0, 0], [-1500.123, 1340.789], [2450.4, -2320.6],
    grid.minLocalMeters, [grid.minLocalMeters[0] + (grid.columns - 1) * grid.stepMeters,
      grid.minLocalMeters[1] + (grid.rows - 1) * grid.stepMeters]];
  for (const point of points) {
    const selected = alignment.toLocal(alignment.toBd09(point));
    assert.ok(Math.hypot(selected[0] - point[0], selected[1] - point[1]) < 0.001);
  }
  assert.equal(alignment.toBd09([10000, 10000]), null);
  assert.equal(alignment.toBd09([NaN, 0]), null);
  assert.equal(alignment.toLocal([121, 31]), null);
});

test("broken, shifted or degenerate alignment cannot silently use the old affine mapping", () => {
  for (const corrupt of [value => { delete value.alignment; },
    value => { value.alignment.pointsBd09.pop(); },
    value => { value.alignment.pointsBd09[0][0] = NaN; },
    value => { value.alignment.minLocalMeters = [100000, 100000]; },
    value => { value.alignment.pointsBd09.fill(value.centerBd09); }]) {
    const value = structuredClone(asset);
    corrupt(value);
    assert.throws(() => preparedMapGeometry(value, expected), /网格/);
    assert.deepEqual(preparedMapCenter(value), asset.centerBd09);
  }
});

test("long edges and polygon rings are sampled before nonlinear projection without changing the graph", () => {
  const path = [[0, 0], [1000, 0]], original = structuredClone(path);
  const sampled = sampleLocalPath(path);
  assert.equal(sampled.length, 9);
  assert.deepEqual(sampled[0], path[0]);
  assert.deepEqual(sampled.at(-1), path.at(-1));
  assert.deepEqual(path, original);
  const area = { type: "MultiPolygon", coordinates: [[[[0, 0], [1000, 0], [1000, 500], [0, 0]],
    [[100, 100], [200, 100], [200, 200], [100, 100]]]] };
  const dense = sampleLocalArea(area);
  assert.ok(dense.coordinates[0][0].length > area.coordinates[0][0].length);
  assert.equal(dense.coordinates[0].length, 2, "the polygon's hole is preserved");
  assert.deepEqual(dense.coordinates[0][0][0], dense.coordinates[0][0].at(-1));
});
