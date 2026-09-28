import test from "node:test";
import assert from "node:assert/strict";
import {
  localExperimentCategories, localExperimentPlot, selectLocalWalkwayPoint,
} from "../src/localExperimentGeometry.js";

const coordinates = [[121.5, 31.3], [121.501, 31.3], [121.501, 31.301]];
const feature = (properties) => ({ type: "Feature", properties,
  geometry: { type: "LineString", coordinates } });
const result = {
  reachableWalkways: { features: [feature({ edgeId: "walk" })] },
  categorySegments: { features: [
    feature({ edgeId: "walk", category: "shopping", classification: "covered" }),
    feature({ edgeId: "walk", category: "healthcare", classification: "unknown" }),
  ] },
  metadata: { facilityInventoryStatusByCategory: { shopping: "verified", healthcare: "incomplete", education: "verified" } },
};

test("keeps complete BD-09 polylines and separates categories without creating areas", () => {
  const plot = localExperimentPlot(result, "shopping");
  assert.equal(plot.walkways.length, 1);
  assert.equal(plot.segments.length, 1);
  assert.equal(plot.segments[0].classification, "covered");
  assert.equal((plot.walkways[0].path.match(/L/g) ?? []).length, 2);
  assert.equal(plot.walkways[0].path.includes("Z"), false);
  assert.deepEqual(localExperimentCategories(result), ["shopping", "healthcare", "education"]);
  const north = plot.project(coordinates[2]);
  assert.ok(north[1] < plot.project(coordinates[1])[1]);
  const roundTrip = plot.unproject(north);
  assert.ok(Math.abs(roundTrip[0] - coordinates[2][0]) < 1e-9);
  assert.ok(Math.abs(roundTrip[1] - coordinates[2][1]) < 1e-9);
});

test("selects an exact point on a known edge instead of a free off-network connector", () => {
  const plot = localExperimentPlot(result, "shopping");
  const pixel = plot.project([121.50102, 31.3005]);
  const selected = selectLocalWalkwayPoint(pixel, plot.walkways[0], plot);
  assert.equal(selected.originEdgeId, "walk");
  assert.equal(selected.coordType, "bd09ll");
  assert.ok(Math.abs(selected.lng - 121.501) < 1e-9);
  assert.ok(Math.abs(selected.lat - 31.3005) < 1e-9);
  assert.equal(localExperimentPlot({ reachableWalkways: { features: [] } }, "shopping"), null);
});

test("selects WGS-84 points and draws only reachable roads when facilities are absent", () => {
  const wgsResult = { ...result, metadata: { coordType: "wgs84ll" },
    categorySegments: { features: [] } };
  const plot = localExperimentPlot(wgsResult, "");
  const selected = selectLocalWalkwayPoint(plot.project(coordinates[1]), plot.walkways[0], plot);
  assert.equal(selected.coordType, "wgs84ll");
  assert.deepEqual(localExperimentCategories(wgsResult), []);
  assert.deepEqual(plot.segments, []);
});
