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
    BMapLoader: {}, RealMapStage: {}, PoiInventoryPanel: {},
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

test("LIVING CIRCLE is visible on entry, can be toggled, and resets on a fresh visit", t => {
  const app = componentState(t, "App.vue");
  assert.equal(app.livingFooterVisible.value, true);
  app.toggleLivingFooter();
  assert.equal(app.livingFooterVisible.value, false);
  assert.equal(componentState(t, "App.vue").livingFooterVisible.value, true);
  app.toggleLivingFooter();
  assert.equal(app.livingFooterVisible.value, true);
});

test("toggling the wordmark preserves mode, scale, origins and existing analysis results", t => {
  const app = componentState(t, "App.vue");
  app.realCandidate.value = { local: { x: 20, y: 30 } };
  app.cppCandidate.value = { local: { x: 40, y: 50 } };
  app.realAnalysisResult.value = { displayArea: { type: "circle", radius: 1170 } };
  app.cppAnalysisResult.value = result();
  app.setMapZoomTier("result");
  const origins = [app.realCandidate.value, app.cppCandidate.value];
  const results = [app.realAnalysisResult.value, app.cppAnalysisResult.value];
  const overviewId = app.mapOverviewRequestId.value;
  for (let index = 0; index < 6; index += 1) app.toggleLivingFooter();
  assert.equal(app.livingFooterVisible.value, true);
  assert.equal(app.mapMode.value, "synthetic");
  assert.equal(app.mapZoomTier.value, "result");
  assert.equal(app.mapOverviewRequestId.value, overviewId);
  assert.equal(app.realCandidate.value, origins[0]);
  assert.equal(app.cppCandidate.value, origins[1]);
  assert.equal(app.realAnalysisResult.value, results[0]);
  assert.equal(app.cppAnalysisResult.value, results[1]);
  app.toggleLivingFooter();
  app.switchMapMode("real");
  assert.equal(app.livingFooterVisible.value, false, "mode switching must not reopen the footer");
});

test("wordmark toggle stays outside the collapsible footer and exposes its expanded state", () => {
  const source = readFileSync(new URL("../src/App.vue", import.meta.url), "utf8");
  const button = source.match(/<button[^>]*class="header-nav-button living-footer-toggle"[\s\S]*?<\/button>/)?.[0];
  assert.ok(button);
  assert.match(button, /type="button"/);
  assert.match(button, /aria-controls="living-circle-footer"/);
  assert.match(button, /:aria-expanded="livingFooterVisible"/);
  assert.match(button, /@click="toggleLivingFooter"/);
  const footer = source.match(/<footer\b[\s\S]*?>/)?.[0];
  assert.match(footer, /id="living-circle-footer"/);
  assert.doesNotMatch(footer, /\bv-(?:show|if)=/);
  assert.match(footer, /:aria-hidden="!livingFooterVisible"/);
  assert.match(footer, /@transitionend="finishLivingFooterTransition"/);
  assert.match(source, /:layout-transitioning="livingFooterAnimating"/);
  assert.ok(source.indexOf(button) < source.indexOf(footer));
  const css = readFileSync(new URL("../src/demo.css", import.meta.url), "utf8");
  assert.match(css, /\.living-footer\.is-hidden \.living-slice\s*\{\s*animation-play-state:\s*paused;/);
});

function animatedFooter(t, reducedMotion = false) {
  let nextId = 0;
  const pending = new Map();
  const unmount = [];
  const app = componentState(t, "App.vue", {}, {
    window: { ...globalThis.window, matchMedia: () => ({ matches: reducedMotion }) },
    setTimeout: (callback, delay) => {
      const id = ++nextId;
      pending.set(id, { callback, delay });
      return id;
    },
    clearTimeout: id => pending.delete(id),
    onUnmounted: callback => unmount.push(callback),
  });
  t.after(() => unmount.forEach(callback => callback()));
  return { app, pending, unmount };
}

test("footer folding can reverse mid-transition and ignores child transition events", t => {
  const { app, pending } = animatedFooter(t);
  assert.equal(app.livingFooterAnimating.value, false);
  app.toggleLivingFooter();
  assert.equal(app.livingFooterVisible.value, false);
  assert.equal(app.livingFooterAnimating.value, true);
  const firstTimer = [...pending.keys()][0];
  app.toggleLivingFooter();
  assert.equal(app.livingFooterVisible.value, true);
  assert.equal(pending.has(firstTimer), false);
  assert.equal(pending.size, 1, "rapid reversal must replace the fallback, not accumulate timers");
  const footer = {};
  app.finishLivingFooterTransition({ target: {}, currentTarget: footer, propertyName: "height" });
  app.finishLivingFooterTransition({ target: footer, currentTarget: footer, propertyName: "opacity" });
  assert.equal(app.livingFooterAnimating.value, true);
  app.finishLivingFooterTransition({ target: footer, currentTarget: footer, propertyName: "height" });
  assert.equal(app.livingFooterAnimating.value, false);
  assert.equal(pending.size, 0);
});

test("footer transition has a bounded fallback and clears its timer on unmount", t => {
  const { app, pending, unmount } = animatedFooter(t);
  app.toggleLivingFooter();
  const fallback = [...pending.values()][0];
  assert.equal(fallback.delay, 480);
  fallback.callback();
  assert.equal(app.livingFooterAnimating.value, false);
  assert.equal(app.livingFooterVisible.value, false);
  assert.equal(pending.size, 0);
  app.toggleLivingFooter();
  unmount.forEach(callback => callback());
  assert.equal(pending.size, 0);
});

test("reduced motion changes the footer immediately without an animation timer", t => {
  const { app, pending } = animatedFooter(t, true);
  app.toggleLivingFooter();
  assert.equal(app.livingFooterVisible.value, false);
  assert.equal(app.livingFooterAnimating.value, false);
  app.toggleLivingFooter();
  assert.equal(app.livingFooterVisible.value, true);
  assert.equal(pending.size, 0);
});

test("footer animation folds the panel without distorting the letter proportions", () => {
  const css = readFileSync(new URL("../src/demo.css", import.meta.url), "utf8");
  const footer = css.match(/\.living-footer\s*\{([^}]*)\}/)[1];
  const content = css.match(/\.living-footer-content\s*\{([^}]*)\}/)[1];
  assert.match(footer, /height:\s*var\(--living-footer-height\)/);
  assert.match(footer, /transition:\s*height\s+\.42s/);
  assert.match(content, /height:\s*var\(--living-footer-height\)/);
  assert.match(content, /opacity\s+\.24s/);
  assert.match(css, /\.living-footer\.is-hidden\s*\{\s*height:\s*0;/);
  assert.match(css, /\.living-footer\.is-hidden \.living-footer-content\s*\{[^}]*translateY\(14px\)/);
  // Responsive rules change the target variable, never override the collapsed height.
  assert.equal((css.match(/\.living-footer\s*\{[^}]*--living-footer-height:/g) ?? []).length, 3);
  assert.match(css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)")),
    /\.living-footer, \.living-footer-content\s*\{\s*transition:\s*none !important;/);
});

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

test("only quick and expert modes remain, with unchanged API identifiers", t => {
  const app = componentState(t, "App.vue");
  assert.deepEqual(app.mapModes.map(({ id, label }) => [id, label]), [
    ["real", "快速模式"], ["synthetic", "专家模式"],
  ]);
});

for (const [search, expected] of [["", "real"], ["?mode=real", "real"],
  ["?mode=synthetic", "synthetic"], ["?mode=local", "real"], ["?mode=unknown", "real"]]) {
  test(`initial query ${search || "(empty)"} selects ${expected}`, t => {
    const app = componentState(t, "App.vue", {}, {
      window: { ...globalThis.window, location: { search } },
    });
    assert.equal(app.mapMode.value, expected);
  });
}

test("unsupported mode switches preserve the expert state and result viewport", t => {
  const { app } = appWithPendingAnalysis(t);
  app.setMapZoomTier("result");
  const previous = app.cppAnalysisResult.value;
  const requestId = app.mapOverviewRequestId.value;
  app.switchMapMode("local");
  app.switchMapMode("unknown");
  assert.equal(app.mapMode.value, "synthetic");
  assert.equal(app.mapZoomTier.value, "result");
  assert.equal(app.cppAnalysisResult.value, previous);
  assert.equal(app.mapOverviewRequestId.value, requestId);
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
  for (const mode of ["real", "synthetic", "real", "synthetic", "real"]) app.switchMapMode(mode);
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

function svgCamera(stage) {
  const { scale, translateX, translateY } = stage.fallbackView.value;
  return {
    scale,
    centerX: (stage.viewport.value.width / 2 - translateX) / scale,
    centerY: (stage.viewport.value.height / 2 - translateY) / scale,
  };
}

for (const mode of ["preview", "cpp"]) {
  for (const tier of mode === "cpp" ? ["large", "medium", "small", "result"] : ["large", "medium", "small"]) {
    test(`SVG ${mode} ${tier} preserves metres-to-pixels and centre throughout footer folding`, async t => {
      const props = mapProps(tier);
      props.analysisMode = mode;
      props.candidate = { lng: 121.505, lat: 31.333, coordType: "wgs84ll" };
      const stage = componentState(t, "RealMapStage.vue", props);
      stage.stageEl.value = { clientWidth: 1440, clientHeight: 640 };
      stage.updateSize();
      stage.fallbackPan.value = { x: 42, y: -25 };
      const before = svgCamera(stage);
      const overview = stage.overviewResult.value;
      props.layoutTransitioning = true;
      await nextTick();
      for (const height of [660, 720, 800, 900, 850, 700, 640]) {
        stage.stageEl.value = { clientWidth: 1440, clientHeight: height };
        stage.updateSize();
        const camera = svgCamera(stage);
        assert.equal(camera.scale, before.scale);
        assert.ok(Math.abs(camera.centerX - before.centerX) < 1e-9);
        assert.ok(Math.abs(camera.centerY - before.centerY) < 1e-9);
      }
      props.layoutTransitioning = false;
      await nextTick();
      assert.deepEqual(svgCamera(stage), before);
      assert.equal(props.zoomTier, tier);
      assert.equal(stage.overviewResult.value, overview);
      assert.deepEqual(stage.fallbackPan.value, { x: 42, y: -25 });
    });
  }
}

test("instant footer changes and a width resize preserve the SVG camera too", t => {
  const stage = componentState(t, "RealMapStage.vue", mapProps());
  stage.stageEl.value = { clientWidth: 1440, clientHeight: 640 };
  stage.updateSize();
  const before = svgCamera(stage);
  for (const [width, height] of [[1440, 900], [1200, 800], [1440, 640]]) {
    stage.stageEl.value = { clientWidth: width, clientHeight: height };
    stage.updateSize();
    const camera = svgCamera(stage);
    assert.equal(camera.scale, before.scale);
    assert.ok(Math.abs(camera.centerX - before.centerX) < 1e-9);
    assert.ok(Math.abs(camera.centerY - before.centerY) < 1e-9);
  }
});

test("an explicit SVG overview request refits to the new size after a footer resize", async t => {
  const props = mapProps("result");
  const stage = componentState(t, "RealMapStage.vue", props);
  stage.stageEl.value = { clientWidth: 1440, clientHeight: 640 };
  stage.updateSize();
  const before = svgCamera(stage);
  stage.stageEl.value = { clientWidth: 1440, clientHeight: 900 };
  stage.updateSize();
  assert.equal(svgCamera(stage).scale, before.scale);
  props.overviewRequestId += 1;
  await nextTick();
  assert.equal(svgCamera(stage).scale, overview.cppOverviewFit(props.analysisResult, 1440, 900).scale);
  assert.ok(svgCamera(stage).scale > before.scale);
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

function wheelApp(t) {
  let now = 0;
  const scrolls = [];
  const TestElement = class { closest() { return null; } };
  const app = componentState(t, "App.vue", {}, { performance: { now: () => now }, Element: TestElement });
  app.pageViewport.value = { clientHeight: 900, scrollTop: 0, scrollTo: view => scrolls.push(view) };
  const event = (deltaY, overrides = {}) => ({
    deltaY, deltaX: 0, deltaMode: 0, target: new TestElement(),
    defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    ...overrides,
  });
  return { app, scrolls, event, TestElement, at: time => { now = time; } };
}

for (const mode of ["real", "synthetic"]) {
  test(`${mode} map wheels use the existing zoom state without turning pages`, t => {
    const { app, scrolls, event, at } = wheelApp(t);
    app.switchMapMode(mode);
    for (const [time, delta, expected] of [[0, -120, "large"], [500, 120, "medium"], [1000, 120, "small"]]) {
      at(time);
      const input = event(delta);
      app.handleMapWheel(input);
      assert.equal(input.defaultPrevented, true);
      assert.equal(input.stopped, true);
      app.handlePageWheel(input); // Defensive check even if a parent received it.
      assert.equal(app.mapZoomTier.value, expected);
      assert.equal(app.activePage.value, 0);
    }
    assert.equal(scrolls.length, 0);
  });
}

test("map wheel limits still consume scrolling and do not leak old page-turn distance", t => {
  const { app, scrolls, event, at } = wheelApp(t);
  app.handlePageWheel(event(80));
  assert.equal(app.pageWheelDistance, 80);
  app.setMapZoomTier("large");
  const up = event(-1200);
  app.handleMapWheel(up);
  assert.equal(up.defaultPrevented, true);
  assert.equal(app.mapZoomTier.value, "large");
  app.setMapZoomTier("small");
  at(500);
  const down = event(1200);
  app.handleMapWheel(down);
  assert.equal(down.defaultPrevented, true);
  assert.equal(down.stopped, true);
  assert.equal(app.mapZoomTier.value, "small");
  assert.equal(app.pageWheelDistance, 0);
  app.handlePageWheel(event(100));
  assert.equal(app.activePage.value, 0);
  app.handlePageWheel(event(60));
  assert.equal(app.activePage.value, 1);
  assert.equal(scrolls.length, 1, "wheel outside the map must still turn the page");
});

test("map pinch keeps browser zoom; horizontal scrolling does not change tiers or turn pages", t => {
  const { app, scrolls, event } = wheelApp(t);
  const pinch = event(-120, { ctrlKey: true });
  app.handleMapWheel(pinch);
  assert.equal(pinch.stopped, true);
  assert.equal(pinch.defaultPrevented, false);
  const horizontal = event(10, { deltaX: 100 });
  app.handleMapWheel(horizontal);
  assert.equal(horizontal.defaultPrevented, true);
  assert.equal(horizontal.stopped, true);
  assert.equal(app.mapZoomTier.value, "medium");
  assert.equal(scrolls.length, 0);
});

test("the report keeps in-panel scrolling without turning pages", t => {
  const { app, scrolls, event, TestElement } = wheelApp(t);
  const target = new TestElement();
  target.closest = () => ({ scrollHeight: 1200, clientHeight: 600, scrollTop: 0 });
  const input = event(120, { target });
  app.handlePageWheel(input);
  assert.equal(input.defaultPrevented, false);
  assert.equal(app.mapZoomTier.value, "medium");
  assert.equal(scrolls.length, 0);
});

test("buttons reset wheel cooldown, and a full-circle wheel change preserves analysis results", t => {
  const { app, event } = wheelApp(t);
  const previous = result();
  app.cppAnalysisResult.value = previous;
  const stored = app.cppAnalysisResult.value;
  app.setMapZoomTier("result");
  const overviewId = app.mapOverviewRequestId.value;
  app.handleMapWheel(event(-120));
  assert.equal(app.mapZoomTier.value, "medium");
  assert.equal(app.mapOverviewRequestId.value, overviewId);
  assert.equal(app.cppAnalysisResult.value, stored);
  app.setMapZoomTier("large");
  app.handleMapWheel(event(120));
  assert.equal(app.mapZoomTier.value, "medium", "button input resets the old wheel cooldown");
});

test("wheel interception is non-passive and captures the whole map before the SDK", () => {
  const source = readFileSync(new URL("../src/App.vue", import.meta.url), "utf8");
  assert.match(source, /class="map-frame"[^>]*@wheel\.capture="handleMapWheel"/);
  assert.doesNotMatch(source, /@wheel\.capture\.passive/);
});
