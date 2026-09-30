import test from "node:test";
import assert from "node:assert/strict";
import { poiCandidates, visiblePois, poiAccessLabel, poiCacheLabel, poiCategoryCounts, poiEmptyLabel, poiSearchProgress, poiCategoryStyles } from "../src/poiFacilities.js";
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
test("all valid circle POIs are retained without a display cap or a modeled entrance", () => {
  const categories = ["education", "healthcare", "shopping", "public_service", "dining"];
  const features = Array.from({ length: 240 }, (_, index) =>
    feature(`poi-${index}`, categories[index % categories.length], true, index % 2 ? false : null));
  features[0].properties.categories.push("shopping");
  features.push(feature("outside", "education", false, true));
  const many = { poiFacilities: { coordType: "bd09ll", features } };
  assert.equal(visiblePois(many).length, 240);
  assert.equal(visiblePois(many, "shopping").length, 49);
  assert.equal(visiblePois(many, "healthcare").length, 48);
  assert.equal(visiblePois(many, "dining").length, 48);
  assert.ok(visiblePois(many).every(poi => poi.modelReachable !== true));
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
  assert.equal(rows.length, 5);
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
test("removed market and pharmacy categories are hidden even in old normalized reports", () => {
  const oldReport = { poiFacilities: { coordType: "bd09ll", features: [
    feature("market", "market", true), feature("pharmacy", "pharmacy", true),
    feature("shop", "shopping", true),
  ] } };
  assert.deepEqual(visiblePois(oldReport).map(poi => poi.id), ["shop"]);
  assert.equal(visiblePois(oldReport, "market").length, 0);
  assert.equal(visiblePois(oldReport, "pharmacy").length, 0);
  const normalized = { poiFacilities: oldReport.poiFacilities.features.map(f => ({ ...f.properties,
    bd09: f.geometry.coordinates, point: [20, -40] })) };
  assert.deepEqual(visiblePois(normalized).map(poi => poi.id), ["shop"]);
  assert.equal(poiCategoryCounts(normalized).length, 5);
});
test("dining is styled, filtered and counted in raw and normalized reports", () => {
  const diningReport = { poiFacilities: { coordType: "bd09ll", features: [
    feature("restaurant", "dining", true, true), feature("outside-food", "dining", false, true),
    feature("shop", "shopping", true),
  ] }, poiCategories: [{ category: "dining", queriedCount: 2 }] };
  assert.equal(poiCategoryStyles.dining.label, "餐饮");
  assert.equal(poiCategoryStyles.dining.glyph, "餐");
  assert.deepEqual(visiblePois(diningReport, "dining").map(poi => poi.id), ["restaurant"]);
  const row = poiCategoryCounts(diningReport).find(item => item.category === "dining");
  assert.equal(row.queried, 2);
  assert.equal(row.displayed, 1);
  assert.equal(row.modelReachable, 1);
  const normalized = { ...diningReport, poiFacilities: poiCandidates(diningReport) };
  assert.deepEqual(visiblePois(normalized, "dining").map(poi => poi.id), ["restaurant"]);
});
test("partial tile searches explain cached continuation without encouraging a forced refresh", () => {
  const r = { metadata: { poi: { status: "partial", plannedQueries: 28, completedQueries: 10,
    requestBudgetReached: true } } };
  assert.match(poiSearchProgress(r), /10\/28.*未完整.*请求预算.*复用缓存.*不必强制刷新/);
  assert.match(poiSearchProgress({ metadata: { poi: { status: "ready", plannedQueries: 28,
    completedQueries: 28 } } }), /28\/28.*非设施普查/);
});

test("quota and auth failures explain the actual cause instead of implying zero facilities", () => {
  const info = { status: "partial", plannedQueries: 84, completedQueries: 30 };
  assert.match(poiSearchProgress({ metadata: { poi: { ...info, quotaLimited: true } } }),
    /百度限流.*限制恢复.*缓存已保留.*不要强制刷新/);
  assert.match(poiSearchProgress({ metadata: { poi: { ...info, authFailed: true } } }),
    /鉴权.*服务端 AK.*缓存已保留/);
});
