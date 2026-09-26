import test from "node:test";
import assert from "node:assert/strict";
import { normalizeCppReport, requestCppMapAnalysis } from "../src/cppAnalysisClient.js";
import { geometryToSvgPath } from "../src/mapGeometry.js";

const origin = { x: -367.8, y: -37.5 };
const report = {
  metadata: { networkSource: "synthetic", coordType: "wgs84ll", originSnapMeters: 4.9,
    originAccessSeconds: 4.9 / 1.3, snappedOrigin: [121.501, 31.333] },
  warnings: ["SYNTHETIC_NETWORK_NOT_REAL_WORLD"],
  isochrone: { geometry: { type: "MultiPolygon", coordinates: [[[
    [121.5, 31.33], [121.501, 31.33], [121.501, 31.331],
    [121.5, 31.33],
  ]]] } },
  reachableWalkways: { features: [{
    geometry: { type: "LineString", coordinates: [
      [121.5, 31.33], [121.5005, 31.33], [121.501, 31.33],
    ] },
    properties: { edgeId: "synthetic-sidewalk:left" },
  }] },
};

test("normalizes the Python/C++ WGS-84 response to the SVG map contract", () => {
  const normalized = normalizeCppReport(report, origin);
  assert.equal(normalized.source, "synthetic-cpp-engine");
  assert.equal(normalized.coordinateSystem, "preview-local-v1");
  assert.equal(normalized.displayArea.type, "polygon");
  assert.equal(normalized.displayArea.geometry.type, "MultiPolygon");
  assert.equal(normalized.routeSegments.length, 2);
  assert.deepEqual(normalized.routeSegments.map((segment) => segment.id), [
    "synthetic-sidewalk:left:0", "synthetic-sidewalk:left:1",
  ]);
  assert.ok(normalized.routeSegments[0].points[0][1] > 0,
    "the browser preview must retain its south-positive Y axis");
  assert.equal(normalized.summary.originSnapMeters, 4.9);
  assert.equal(normalized.summary.originAccessSeconds, 4.9 / 1.3);
  assert.equal(normalized.accessLink.verified, false);
  assert.equal(normalized.accessLink.points.length, 2);
  assert.throws(() => normalizeCppReport({ ...report,
    metadata: { ...report.metadata, networkSource: "manual" } }, origin),
  /NOT_SYNTHETIC_WGS84_REPORT/);
});

test("C++ mode renders multiple computed polygon rings, never a fixed circle", () => {
  const geometry = { type: "MultiPolygon", coordinates: [
    [
      [[121.5, 31.33], [121.501, 31.33], [121.501, 31.331], [121.5, 31.33]],
      [[121.5002, 31.3302], [121.5004, 31.3302], [121.5003, 31.3304],
        [121.5002, 31.3302]],
    ],
    [[[121.502, 31.332], [121.503, 31.332], [121.502, 31.333],
      [121.502, 31.332]]],
  ] };
  const normalized = normalizeCppReport({ ...report, isochrone: { geometry } }, origin);
  const svgPath = geometryToSvgPath(normalized.displayArea.geometry, (point) => point);
  assert.equal(normalized.displayArea.type, "polygon");
  assert.equal(normalized.displayArea.geometry.coordinates.length, 2);
  assert.equal((svgPath.match(/M/g) ?? []).length, 3);
  assert.equal((svgPath.match(/Z/g) ?? []).length, 3);
});

test("synthetic client posts a WGS-84 centre, polls the API, and returns the map result", async () => {
  const calls = [];
  const responses = [
    { analysisId: "demo-1", status: "queued" },
    { status: "completed", result: report },
  ];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => responses.shift() };
  };
  const result = await requestCppMapAnalysis({ origin }, { fetchImpl });
  assert.equal(result.summary.routeSegmentCount, 2);
  assert.deepEqual(calls.map((call) => call.url), [
    "/api/v1/synthetic-analyses", "/api/v1/analyses/demo-1",
  ]);
  const request = JSON.parse(calls[0].options.body);
  assert.equal(request.center.coordType, "wgs84ll");
  assert.equal(request.minutes, 15);
});
