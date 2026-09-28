import test from "node:test";
import assert from "node:assert/strict";
import { poiCandidates, visiblePois, poiAccessLabel, poiCacheLabel, poiCategoryCounts, poiEmptyLabel } from "../src/poiFacilities.js";
import config from "../vite.config.js";

const feature = (id, category, inside, modelReachable = null) => ({ type: "Feature",
  geometry: { type: "Point", coordinates: [121.51, 31.31] },
  properties: { id, category, categories: [category], name: id,
    insideDisplayPolygon: inside, modelReachable, localPointMeters: [20, 40], accessStatus: "missing_navigation_point" } });
const report = { poiFacilities: { coordType: "bd09ll", features: [
  feature("school", "education", true), feature("shop", "shopping", true, true),
  feature("hospital", "healthcare", false, true),
] } };

test("native BD-09 point is preserved; only graph meters are converted to SVG south-positive Y", () => {
  const [poi] = poiCandidates(report);
  assert.deepEqual(poi.bd09, [121.51, 31.31]);
  assert.deepEqual(poi.point, [20, -40]);
  assert.deepEqual(poiCandidates({ poiFacilities: { ...report.poiFacilities, coordType: "wgs84ll" } }), []);
});
test("circle containment does not mean network reachability and respects category filtering", () => {
  assert.deepEqual(visiblePois(report).map(item => item.id), ["school", "shop"]);
  assert.equal(visiblePois(report, "education")[0].modelReachable, null);
  assert.equal(visiblePois(report, "healthcare").length, 0);
  assert.match(poiAccessLabel(visiblePois(report)[0]), /尚未接入/);
});
test("local mode uses street proximity, never a made-up polygon", () => {
  const local = { ...report, mode: "local_experiment" };
  assert.deepEqual(visiblePois(local).map(item => item.id), ["shop", "hospital"]);
});
test("cache source distinguishes warm reads, fresh API calls, stale data and failures", () => {
  const result = poi => ({ ...report, metadata: { poi } });
  assert.equal(poiCacheLabel(result({ status: "ready", apiRequests: 0, cacheHits: 8 })), "缓存命中 8 项");
  assert.equal(poiCacheLabel(result({ status: "ready", apiRequests: 2, cacheHits: 6 })), "API 2 次 · 缓存 6 项");
  assert.match(poiCacheLabel(result({ status: "partial", stalePages: 1 })), /旧缓存/);
  assert.equal(poiCacheLabel(result({ status: "unavailable" })), "检索不可用");
  assert.equal(poiCacheLabel(report), "待加载");
});
test("category statistics count only displayed candidates as model reachable", () => {
  const result = { ...report, poiCategories: [{ category: "healthcare", queriedCount: 15 }] };
  const rows = poiCategoryCounts(result);
  assert.equal(rows.length, 4);
  const hospital = rows.find(row => row.category === "healthcare");
  assert.equal(hospital.queried, 15);
  assert.equal(hospital.displayed, 0);
  assert.equal(hospital.modelReachable, 0, "a model-reachable hospital outside the display polygon is not a circle POI");
  assert.equal(rows.find(row => row.category === "shopping").modelReachable, 1);
  assert.equal(rows.find(row => row.category === "education").queried, null);
});
test("empty category explains query versus circle counts without claiming real scarcity", () => {
  const result = { ...report, poiCategories: [{ category: "healthcare", queriedCount: 15 }] };
  assert.match(poiEmptyLabel(result, "healthcare"), /近似圈内暂无医院.*15 个.*不代表真实设施匮乏/);
  assert.match(poiEmptyLabel({ ...result, mode: "local_experiment" }, "healthcare"), /可达街段附近/);
  assert.match(poiEmptyLabel({ metadata: { poi: { status: "unavailable" } } }), /未取得设施数据/);
});
test("Vite exposes only the browser AK prefix, not the legacy server key", () => {
  assert.deepEqual(config.envPrefix, ["VITE_BAIDU_BROWSER_"]);
  assert.ok(!config.envPrefix.some(prefix => "VITE_BAIDU_SERVER_AK".startsWith(prefix)));
});
