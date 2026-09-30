import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizeLocalExperimentResult,
  requestLocalExperiment,
} from "../src/localExperimentClient.js";

const point = { lng: 121.51, lat: 31.33, coordType: "bd09ll" };
const line = (coordinates, properties = {}) => ({
  type: "Feature", geometry: { type: "LineString", coordinates },
  properties: { edgeId: "walk", kind: "sidewalk", ...properties },
});
const result = {
  mode: "local_experiment",
  label: "3 分钟局部路网实验",
  thresholdSeconds: 180,
  notForMainReport: true,
  reachableWalkways: { type: "FeatureCollection", features: [
    line([[121.51, 31.33], [121.511, 31.33]], { edgeId: "walk" }),
  ] },
  categorySegments: { type: "FeatureCollection", features: [
    line([[121.51, 31.33], [121.5105, 31.33]],
      { edgeId: "walk", category: "healthcare", classification: "covered" }),
    line([[121.5105, 31.33], [121.511, 31.33]],
      { edgeId: "walk", category: "healthcare", classification: "unknown" }),
  ] },
  warnings: ["LOCAL_REACHABILITY_MAY_BE_TRUNCATED"],
  metadata: { networkSource: "synthetic", coordType: "bd09ll" },
};

test("accepts isolated local results while preserving BD-09 or WGS-84", () => {
  const normalized = normalizeLocalExperimentResult(result);
  assert.equal(normalized.source, "local-experiment");
  assert.equal(normalized.coordinateSystem, "bd09ll");
  assert.equal(normalized.categorySegments.features.length, 2);
  assert.throws(() => normalizeLocalExperimentResult({ ...result, notForMainReport: false }),
    /INVALID_LOCAL_EXPERIMENT_RESULT/);
  assert.equal(normalizeLocalExperimentResult({ ...result,
    metadata: { ...result.metadata, coordType: "wgs84ll" } }).coordinateSystem, "wgs84ll");
  assert.throws(() => normalizeLocalExperimentResult({ ...result,
    metadata: { ...result.metadata, coordType: "gcj02ll" } }), /INVALID_LOCAL_EXPERIMENT_RESULT/);
  assert.throws(() => normalizeLocalExperimentResult({ ...result,
    categorySegments: { type: "FeatureCollection", features: [
      line([[121.51, 31.33], [121.511, 31.33]],
        { category: "healthcare", classification: "gray" }),
    ] } }), /INVALID_LOCAL_EXPERIMENT_CLASSIFICATION/);
});

test("posts BD-09 point to the independent endpoint and polls the same path", async () => {
  const calls = [];
  const responses = [
    { analysisId: "local-1", status: "queued" },
    { analysisId: "local-1", status: "completed", result },
  ];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => responses.shift() };
  };
  const received = await requestLocalExperiment({ candidate: point,
    originEdgeId: "left_side" }, { fetchImpl });
  assert.equal(received.metadata.networkSource, "synthetic");
  assert.deepEqual(calls.map((call) => call.url), [
    "/api/v1/local-experiments", "/api/v1/local-experiments/local-1",
  ]);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    center: point, originEdgeId: "left_side", includePois: true, refreshPois: false,
  });
  await assert.rejects(requestLocalExperiment({ candidate: { ...point,
    coordType: "gcj02ll" } }, { fetchImpl }), /LOCAL_EXPERIMENT_REQUIRES_EXPLICIT_COORDINATE_TYPE/);
});

test("uses WGS-84 for the existing modeled graph without creating gray segments", async () => {
  const wgsPoint = { ...point, coordType: "wgs84ll" };
  const wgsResult = { ...result,
    categorySegments: { type: "FeatureCollection", features: [] },
    metadata: { ...result.metadata, coordType: "wgs84ll", grayZoneStatus: "data_insufficient" },
  };
  const bodies = [];
  const fetchImpl = async (_url, options) => {
    if (options?.body) bodies.push(JSON.parse(options.body));
    return { ok: true, json: async () => options?.body
      ? { analysisId: "wgs-1" } : { status: "completed", result: wgsResult } };
  };
  const received = await requestLocalExperiment({ candidate: wgsPoint }, { fetchImpl });
  assert.equal(bodies[0].center.coordType, "wgs84ll");
  assert.equal(received.coordinateSystem, "wgs84ll");
  assert.deepEqual(received.categorySegments.features, []);
  await assert.rejects(requestLocalExperiment({ candidate: point }, { fetchImpl }),
    /LOCAL_EXPERIMENT_COORDINATE_TYPE_MISMATCH/);
});

test("reports absent surveyed data without substituting a synthetic result", async () => {
  const fetchImpl = async () => ({
    ok: false, status: 422,
    json: async () => ({ detail: { code: "UNSUPPORTED_AREA",
      message: "local graph not configured" } }),
  });
  await assert.rejects(requestLocalExperiment({ candidate: point }, { fetchImpl }),
    /UNSUPPORTED_AREA/);
});
