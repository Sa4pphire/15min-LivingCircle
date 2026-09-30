import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parse, compileScript } from "vue/compiler-sfc";
import { computed, effectScope, nextTick, reactive, ref, shallowRef, watch } from "vue";
import * as geometry from "../src/mapGeometry.js";
import * as zoom from "../src/mapZoom.js";
import * as overview from "../src/cppOverview.js";
import * as pois from "../src/poiFacilities.js";
import { baiduMapStyle } from "../src/baiduMapStyle.js";
import * as mapAsset from "../src/mapAsset.js";

const originalGlobals = Object.fromEntries(["window", "requestAnimationFrame", "cancelAnimationFrame"]
  .map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
globalThis.window = { location: { search: "?mode=synthetic" }, matchMedia: () => ({ matches: true }) };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
after(() => {
  for (const [name, descriptor] of Object.entries(originalGlobals)) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
  }
});

// Run the real script-setup state/watchers with Vue reactivity. Only DOM
// lifecycle and external APIs are stubbed; no test-only routing logic or AK.
function componentState(t, filename, props = {}, overrides = {}) {
  const unmount = [];
  const bindings = {
    computed, nextTick, ref, shallowRef, watch,
    onMounted: () => {}, onUnmounted: callback => unmount.push(callback),
    ...geometry, ...zoom, ...overview, ...pois, ...mapAsset, baiduMapStyle,
    BMapLoader: {}, RealMapStage: {}, LocalExperimentStage: {}, PoiInventoryPanel: {},
    boundaryWgs: JSON.parse(readFileSync(new URL("../src/data/demoBoundary.wgs84.json", import.meta.url))),
    preparedMapAsset: JSON.parse(readFileSync(new URL("../src/data/demoMap.bd09.json", import.meta.url))),
    requestMapAnalysis: () => { throw new Error("Unexpected preview API call"); },
    requestCppMapAnalysis: () => { throw new Error("Unexpected C++ API call"); },
    ...overrides,
  };
  const source = readFileSync(new URL(`../src/${filename}`, import.meta.url), "utf8");
  const { descriptor } = parse(source);
  const script = compileScript(descriptor, { id: `viewport-test-${filename}` }).content
    .replace(/^import\s+[\s\S]*?;\r?\n/gm, "")
    .replaceAll("import.meta.env", "({})")
    .replace("export default", "return");
  const component = new Function(...Object.keys(bindings), script)(...Object.values(bindings));
  const scope = effectScope();
  const state = scope.run(() => component.setup(props, { expose() {}, emit() {} }));
  t.after(() => { unmount.forEach(callback => callback()); scope.stop(); });
  return state;
}

function result(size = 500) {
  return {
    source: "synthetic-cpp-engine", coordinateSystem: "preview-local-v1",
    origin: { x: 0, y: 0 }, routeSegments: [], poiFacilities: [],
    displayArea: { type: "polygon", geometry: { type: "MultiPolygon",
      coordinates: [[[[-size, -size], [size, -size], [size, size], [-size, size], [-size, -size]]]] } },
  };
}

function appWithPendingAnalysis(t) {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const app = componentState(t, "App.vue", {}, { requestCppMapAnalysis: () => response });
  app.cppCandidate.value = { local: { x: 0, y: 0 } };
  app.cppAnalysisResult.value = result();
  return { app, finish };
}

for (const tier of ["large", "medium", "small", "result"]) {
  test(`C++ completion and recalculation keep the ${tier} tier`, async t => {
    const { app, finish } = appWithPendingAnalysis(t);
    app.setMapZoomTier(tier);
    const requestId = app.mapOverviewRequestId.value;
    const pending = app.runCppAnalysis();
    assert.equal(app.cppAnalysisResult.value, null);
    assert.equal(app.mapZoomTier.value, tier);
    finish(result(1200));
    await pending;
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(app.cppAnalysisState.value, "complete");
    assert.equal(app.mapZoomTier.value, tier);
    assert.equal(app.mapOverviewRequestId.value, requestId);
  });
}

test("changing tiers while C++ is running keeps the latest user choice", async t => {
  const { app, finish } = appWithPendingAnalysis(t);
  const pending = app.runCppAnalysis();
  app.setMapZoomTier("small");
  finish(result());
  await pending;
  assert.equal(app.mapZoomTier.value, "small");
});

test("the overview button can explicitly refit even when already selected", t => {
  const { app } = appWithPendingAnalysis(t);
  app.setMapZoomTier("result");
  const previous = app.mapOverviewRequestId.value;
  app.setMapZoomTier("result");
  assert.equal(app.mapZoomTier.value, "result");
  assert.equal(app.mapOverviewRequestId.value, previous + 1);
});

test("mode labels keep the two analysis modes and expose blind-zone toggle", t => {
  const app = componentState(t, "App.vue");
  assert.deepEqual(app.mapModes.map(({ id, label }) => [id, label]), [
    ["real", "真实区域"], ["synthetic", "专家模式"], ["blind", "盲区显示"],
  ]);
});

test("clicking the selected expert mode preserves the overview and results", t => {
  const { app } = appWithPendingAnalysis(t);
  app.setMapZoomTier("result");
  const previous = app.cppAnalysisResult.value;
  const requestId = app.mapOverviewRequestId.value;
  app.switchMapMode("synthetic");
  assert.equal(app.mapZoomTier.value, "result");
  assert.equal(app.cppAnalysisResult.value, previous);
  assert.equal(app.mapOverviewRequestId.value, requestId);
});

test("rapid mode switches keep independent results and the latest selected mode", t => {
  const { app } = appWithPendingAnalysis(t);
  app.realCandidate.value = { local: { x: 30, y: 10 } };
  app.realAnalysisResult.value = { displayArea: { type: "circle", radius: 1170 } };
  const quickResult = app.realAnalysisResult.value;
  const expertResult = app.cppAnalysisResult.value;
  app.setMapZoomTier("large");
  for (const mode of ["real", "synthetic", "local", "synthetic", "real"]) app.switchMapMode(mode);
  assert.equal(app.mapMode.value, "real");
  assert.equal(app.mapZoomTier.value, "large");
  assert.equal(app.realAnalysisResult.value, quickResult);
  assert.equal(app.cppAnalysisResult.value, expertResult);
});

function mapProps(tier = "medium") {
  return reactive({ candidate: null, analysisResult: result(), zoomTier: tier,
    overviewRequestId: 0, analysisMode: "cpp", selectionDisabled: false });
}

test("C++ route overlays use the calibrated local transform, not WGS84 or a viewport affine", t => {
  const props = mapProps();
  const stage = componentState(t, "RealMapStage.vue", props);
  stage.loadPreparedBoundary();
  const asset = JSON.parse(readFileSync(new URL("../src/data/demoMap.bd09.json", import.meta.url)));
  const points = asset.input.boundaryCoordinatesWgs84.slice(0, 2)
    .map(point => geometry.wgsToLocal(point, asset.input.originWgs84));
  props.analysisResult.routeSegments = [{ id: "test-road", points }];
  const pointToPixel = point => ({ x: (point.lng - asset.centerBd09[0]) * 111320,
    y: (point.lat - asset.centerBd09[1]) * 111320 });
  stage.map = { pointToPixel, removeEventListener() {} };
  stage.BMap = { Point: class { constructor(lng, lat) { this.lng = lng; this.lat = lat; } } };
  stage.mapState.value = "ready";
  const actual = stage.liveDemoResult.value.routes[0].d.match(/-?\d+(?:\.\d+)?/g).map(Number);
  const expectedPixels = asset.boundary.geometry.coordinates[0].slice(0, 2)
    .flatMap(([lng, lat]) => Object.values(pointToPixel({ lng, lat })));
  actual.forEach((value, index) => assert.ok(Math.abs(value - expectedPixels[index]) < 0.5));
});

for (const tier of ["large", "medium", "small"]) {
  test(`SVG result updates preserve ${tier} scale and pan`, async t => {
    const props = mapProps(tier);
    const map = componentState(t, "RealMapStage.vue", props);
    map.viewport.value = { width: 1600, height: 900 };
    map.fallbackPan.value = { x: 42, y: -25 };
    const view = { ...map.fallbackView.value };
    props.analysisResult = null;
    await nextTick();
    assert.deepEqual(map.fallbackView.value, view);
    props.analysisResult = result(2000);
    await nextTick();
    assert.deepEqual(map.fallbackView.value, view);
  });
}

test("SVG overview stays fixed during recalculation until a manual refit", async t => {
  const props = mapProps();
  const map = componentState(t, "RealMapStage.vue", props);
  map.viewport.value = { width: 1600, height: 900 };
  props.zoomTier = "result";
  props.overviewRequestId += 1;
  await nextTick();
  const view = { ...map.fallbackView.value };
  props.analysisResult = null;
  await nextTick();
  assert.deepEqual(map.fallbackView.value, view);
  props.analysisResult = result(2000);
  await nextTick();
  assert.deepEqual(map.fallbackView.value, view);
  props.overviewRequestId += 1;
  await nextTick();
  assert.ok(map.fallbackView.value.scale < view.scale);
});

test("native Baidu map only refits for an explicit view action, not new results", async t => {
  const props = mapProps();
  const stage = componentState(t, "RealMapStage.vue", props);
  let fitCalls = 0;
  let cameraCalls = 0;
  stage.map = {
    getViewport: () => {
      fitCalls += 1;
      return { zoom: 15, center: { lng: 121.5, lat: 31.33 } };
    },
    getZoom: () => 15, getCenter: () => ({ lng: 121.5, lat: 31.33 }),
    centerAndZoom: () => { cameraCalls += 1; },
    removeEventListener() {},
  };
  stage.BMap = { Point: class { constructor(lng, lat) { this.lng = lng; this.lat = lat; } } };
  stage.displayCornersBd09.value = [[121.4, 31.4], [121.6, 31.4], [121.6, 31.2], [121.4, 31.2]];
  stage.mapState.value = "ready";
  stage.viewport.value = { width: 1600, height: 900 };
  props.zoomTier = "result";
  props.overviewRequestId += 1;
  await nextTick();
  assert.equal(fitCalls, 1);
  assert.equal(cameraCalls, 1);
  props.analysisResult = null;
  await nextTick();
  props.analysisResult = result(2000);
  await nextTick();
  assert.equal(fitCalls, 1);
  assert.equal(cameraCalls, 1);
  props.overviewRequestId += 1;
  await nextTick();
  assert.equal(fitCalls, 2);
  assert.equal(cameraCalls, 2);
});

function nativeZoomStage(t, mode = "preview", animated = false) {
  const props = mapProps();
  props.analysisMode = mode;
  const stage = componentState(t, "RealMapStage.vue", props);
  stage.BMap = { Point: class { constructor(lng, lat) { this.lng = lng; this.lat = lat; } } };
  stage.displayCornersBd09.value = [[121.4, 31.4], [121.6, 31.4], [121.6, 31.2], [121.4, 31.2]];
  stage.viewport.value = { width: 1600, height: 900 };
  stage.mapState.value = "ready";
  const commands = [];
  let currentZoom = 14;
  let legacyFitCalls = 0;
  let draggable;
  const fittedCenter = new stage.BMap.Point(121.5, 31.3);
  stage.map = {
    // A viewport change is asynchronous: getZoom still reflects the OLD view.
    // The regression must not assume setViewport updates it synchronously.
    setViewport: () => { legacyFitCalls += 1; },
    getViewport: points => ({ center: fittedCenter, zoom: points.length === 4 ? 13 : 16 }),
    getZoom: () => currentZoom, getCenter: () => fittedCenter,
    getMinZoom: () => 3, getMaxZoom: () => 21,
    centerAndZoom: (center, level, options) => {
      currentZoom = level;
      commands.push({ center, level, options, animated: false });
    },
    enableDragging: () => { draggable = true; },
    disableDragging: () => { draggable = false; },
    removeEventListener() {},
    ...(animated ? { flyTo: (center, level, options) => {
      commands.push({ center, level, options, animated: true });
      // Keep the current view stale until the animation finishes.
    } } : {}),
  };
  return { stage, props, commands, fittedCenter,
    legacyFitCalls: () => legacyFitCalls, draggable: () => draggable };
}

for (const mode of ["preview", "cpp"]) {
  test(`Baidu ${mode} tiers use an absolute baseline in both directions, without zoom accumulation`, async t => {
    const { stage, props, commands, legacyFitCalls, draggable } = nativeZoomStage(t, mode);
    stage.fitBaiduViewport();
    assert.equal(commands.at(-1).level, zoom.baiduZoomForTier(13, "medium"));
    for (const tier of ["large", "medium", "small", "medium", "large", "small", "large", "small"]) {
      props.zoomTier = tier;
      await nextTick();
      assert.equal(commands.at(-1).level, zoom.baiduZoomForTier(13, tier), tier);
      assert.equal(draggable(), tier !== "small");
    }
    assert.equal(legacyFitCalls(), 0);
    assert.equal(stage.fittedZoom, 13);
  });
}

test("leaving a fitted C++ overview restores the normal scale baseline", async t => {
  const { stage, props, commands } = nativeZoomStage(t, "cpp");
  stage.fitBaiduViewport();
  props.zoomTier = "result";
  props.overviewRequestId += 1;
  await nextTick();
  assert.equal(commands.at(-1).level, 16);
  props.zoomTier = "small";
  await nextTick();
  assert.equal(commands.at(-1).level, zoom.baiduZoomForTier(13, "small"));
  props.zoomTier = "large";
  await nextTick();
  assert.equal(commands.at(-1).level, zoom.baiduZoomForTier(13, "large"));
});

test("rapid animated tier changes keep targeting the latest absolute scale", async t => {
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = () => ({ matches: false });
  t.after(() => { window.matchMedia = originalMatchMedia; });
  const { stage, props, commands, legacyFitCalls } = nativeZoomStage(t, "preview", true);
  stage.fitBaiduViewport();
  for (const tier of ["large", "small", "medium", "small"]) {
    props.zoomTier = tier;
    await nextTick();
    const command = commands.at(-1);
    assert.equal(command.level, zoom.baiduZoomForTier(13, tier));
    assert.equal(command.animated, true);
    assert.equal(command.options.duration, 420);
  }
  assert.equal(commands.at(-1).level, zoom.baiduZoomForTier(13, "small"));
  assert.equal(legacyFitCalls(), 0);
});

test("large zoom centres on the selected origin; smaller tiers restore the fitted centre", async t => {
  const { stage, props, commands, fittedCenter } = nativeZoomStage(t);
  props.candidate = { lng: 121.51, lat: 31.33, coordType: "bd09ll" };
  await nextTick();
  stage.fitBaiduViewport();
  props.zoomTier = "large";
  await nextTick();
  assert.deepEqual([commands.at(-1).center.lng, commands.at(-1).center.lat], [121.51, 31.33]);
  props.zoomTier = "small";
  await nextTick();
  assert.equal(commands.at(-1).center, fittedCenter);
});
