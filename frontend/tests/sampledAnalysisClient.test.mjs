import test from "node:test";
import assert from "node:assert/strict";
import { requestSampledMapAnalysis } from "../src/sampledAnalysisClient.js";

test("提交采样任务，并把两个坐标系的等时圈整理给地图", async () => {
  const calls = [];
  const report = {
    sourceMode: "baidu-sampled",
    coordinateSystem: "bd09ll",
    approximate: true,
    thresholdSeconds: 900,
    durationSamples: Array(49).fill({}),
    isochrone: {
      type: "MultiPolygon",
      coordinates: [[[
        [121.51, 31.33], [121.52, 31.33],
        [121.51, 31.34], [121.51, 31.33],
      ]]],
    },
    isochroneMeters: {
      type: "MultiPolygon",
      coordinates: [[[
        [0, 0], [100, 0], [0, 100], [0, 0],
      ]]],
    },
    routeSegments: [
      {
        id: "poi:market-1:0",
        category: "market",
        poiUid: "market-1",
        points: [[121.513, 31.337], [121.514, 31.337]],
        distanceMeters: 120,
        durationSeconds: 90,
      },
    ],
    routeCount: 1,
    samplingRouteCount: 1,
    samplingRouteSegments: [
      { id: "sample:1:0", sampleIndex: 1, angleDegrees: 0,
        points: [[121.513, 31.337], [121.515, 31.337]],
        distanceMeters: 240, durationSeconds: 180 },
    ],
  };
  const responses = [
    { analysisId: "sample-1", status: "queued" },
    { status: "completed", result: report },
  ];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => responses.shift() };
  };
  const candidate = {
    lng: 121.513,
    lat: 31.337,
    coordType: "bd09ll",
    local: { x: 20, y: 30 },
  };

  const result = await requestSampledMapAnalysis(candidate, { fetchImpl });

  assert.deepEqual(calls.map((call) => call.url), [
    "/api/v1/sampled-analyses",
    "/api/v1/sampled-analyses/sample-1",
  ]);
  assert.deepEqual(JSON.parse(calls[0].options.body).center, {
    lng: 121.513,
    lat: 31.337,
    coordType: "bd09ll",
  });
  assert.equal(result.source, "baidu-sampled-idw");
  assert.equal(result.displayArea.geometry, report.isochrone);
  assert.deepEqual(
    result.fallbackDisplayArea.geometry.coordinates[0][0][2],
    [20, -70],
  );
  assert.equal(result.summary.sampleCount, 49);
  assert.equal(result.summary.routeSegmentCount, 1);
  assert.equal(result.summary.routeCount, 1);
  assert.equal(result.summary.samplingRouteCount, 1);
  assert.equal(result.samplingRouteSegments.length, 1);
  assert.deepEqual(result.routeSegments[0].points, [
    [121.513, 31.337], [121.514, 31.337],
  ]);
});
