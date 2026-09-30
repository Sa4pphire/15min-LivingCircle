import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parse, compileScript } from "vue/compiler-sfc";
import { computed, effectScope, nextTick, reactive, ref, shallowRef, watch } from "vue";
import * as geometry from "../src/mapGeometry.js";
import * as zoom from "../src/mapZoom.js";
import * as overview from "../src/cppOverview.js";
import * as pois from "../src/poiFacilities.js";
import * as assets from "../src/mapAsset.js";
import { baiduMapStyle } from "../src/baiduMapStyle.js";

const globals = Object.fromEntries(["window", "requestAnimationFrame", "cancelAnimationFrame"]
  .map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
globalThis.window = { location: { search: "" }, matchMedia: () => ({ matches: true }) };
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
after(() => {
  for (const [name, descriptor] of Object.entries(globals)) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
  }
});

const readJson = name => JSON.parse(readFileSync(new URL(`../src/data/${name}`, import.meta.url)));
const prepared = readJson("demoMap.bd09.json");

function stage(t, asset = prepared, loadError = null) {
  const stats = { sdkLoads: 0, maps: 0, fits: 0, selections: 0 };
  const namespace = {
    Point: class { constructor(lng, lat) { this.lng = lng; this.lat = lat; } },
    Map: class {
      constructor() { stats.maps += 1; }
      centerAndZoom(center, zoom) { this.center = center; this.zoom = zoom; }
      getZoom() { return this.zoom; }
      getCenter() { return this.center; }
      getViewport() { stats.fits += 1; return { center: this.center, zoom: 13 }; }
      setViewport() { assert.fail("Do not schedule a viewport change before applying a zoom tier"); }
      addEventListener() {}
      removeEventListener() {}
    },
  };
  Object.defineProperty(namespace, "Convertor", { get() {
    assert.fail("Page initialization must never access the online Convertor");
  } });
  const unmount = [];
  const bindings = {
    computed, nextTick, ref, shallowRef, watch,
    onMounted: () => {}, onUnmounted: callback => unmount.push(callback),
    ...geometry, ...zoom, ...overview, ...pois, ...assets, baiduMapStyle,
    boundaryWgs: readJson("demoBoundary.wgs84.json"), preparedMapAsset: asset,
    PoiInventoryPanel: {}, BMapLoader: { async load() {
      stats.sdkLoads += 1;
      if (loadError) throw loadError;
      return namespace;
    } },
  };
  const source = readFileSync(new URL("../src/RealMapStage.vue", import.meta.url), "utf8");
  const { descriptor } = parse(source);
  const script = compileScript(descriptor, { id: "baidu-lifecycle-test" }).content
    .replace(/^import\s+[\s\S]*?;\r?\n/gm, "")
    .replaceAll("import.meta.env", '({ VITE_BAIDU_BROWSER_AK: "test-browser-key" })')
    .replace("export default", "return");
  const component = new Function(...Object.keys(bindings), script)(...Object.values(bindings));
  const scope = effectScope();
  const props = reactive({ analysisMode: "preview", candidate: null, analysisResult: null,
    zoomTier: "medium", overviewRequestId: 0, selectionDisabled: false });
  const state = scope.run(() => component.setup(props, { expose() {}, emit(event, point) {
    stats.selections += 1;
    stats.lastSelection = point;
  } }));
  state.baiduEl.value = {};
  t.after(() => { unmount.forEach(callback => callback()); scope.stop(); });
  return { state, props, stats };
}

test("base map starts without Convertor and changing modes does not initialize it again", async t => {
  const { state, props, stats } = stage(t);
  await Promise.all([state.setupBaidu(), state.setupBaidu()]);
  assert.equal(state.mapState.value, "ready");
  assert.equal(state.boundaryState.value, "ready");
  assert.equal(stats.maps, 1);
  assert.equal(stats.sdkLoads, 1);
  const fits = stats.fits;
  const instance = state.map;
  for (const mode of ["cpp", "preview", "cpp", "preview"]) {
    props.analysisMode = mode;
    await nextTick();
  }
  assert.equal(state.map, instance);
  assert.equal(stats.maps, 1);
  assert.equal(stats.sdkLoads, 1);
  assert.equal(stats.fits, fits);
});

test("invalid boundary does not hide the base map, but it blocks selection", async t => {
  const bad = structuredClone(prepared);
  bad.boundary = null;
  const { state, stats } = stage(t, bad);
  await state.setupBaidu();
  assert.equal(state.mapState.value, "ready");
  assert.equal(state.fallbackActive.value, false);
  assert.equal(state.boundaryState.value, "error");
  assert.match(state.stateMessage.value, /选区暂不可用/);
  state.selectRegionCenter();
  assert.equal(stats.selections, 0);
});

test("the prepared region centre can be selected on the BD-09 base map", async t => {
  const { state, stats } = stage(t);
  await state.setupBaidu();
  state.selectRegionCenter();
  assert.equal(stats.selections, 1);
  assert.ok(Math.hypot(stats.lastSelection.local.x, stats.lastSelection.local.y) < 0.001,
    "selecting the official BD-09 centre must reach the actual C++ origin, not a point 32 metres away");
});

test("missing local centre is a data error, not an online retry", async t => {
  const { state, stats } = stage(t, { ...prepared, centerBd09: null });
  // Avoid loading fallback JSON in Node; only DOM/data loading is stubbed.
  state.context.value = { features: [] };
  await state.setupBaidu();
  assert.equal(stats.sdkLoads, 0);
  assert.equal(state.mapState.value, "error");
  assert.match(state.mapError.value, /本地 BD-09 地图中心缺失/);
});

test("SDK failure falls back without trying coordinate conversion or leaking errors", async t => {
  const { state, stats } = stage(t, prepared, new Error("private SDK error"));
  state.context.value = { features: [] };
  await state.setupBaidu();
  assert.equal(stats.sdkLoads, 1);
  assert.equal(stats.maps, 0);
  assert.equal(state.mapState.value, "error");
  assert.equal(state.boundaryState.value, "ready");
  assert.doesNotMatch(state.mapError.value, /private|坐标转换|test-browser-key/);
});

test("hidden map does not refit its viewport or request replacement tiles", async t => {
  const { state, stats } = stage(t);
  await state.setupBaidu();
  const fits = stats.fits;
  state.stageEl.value = { clientWidth: 0, clientHeight: 0 };
  state.updateSize();
  assert.equal(stats.fits, fits);
  state.viewport.value = { width: 1600, height: 900 };
  state.stageEl.value = { clientWidth: 1600, clientHeight: 900 };
  state.updateSize();
  assert.equal(stats.fits, fits);
  state.stageEl.value = { clientWidth: 320, clientHeight: 500 };
  state.updateSize();
  assert.equal(stats.fits, fits + 1);
});

test("App renders one retained map after replacing local experiment with blind-zone toggle", () => {
  const app = readFileSync(new URL("../src/App.vue", import.meta.url), "utf8");
  assert.equal((app.match(/<RealMapStage\b/g) ?? []).length, 1);
  assert.match(app, /v-if="sharedMapMounted"/);
  assert.match(app, /showBlindZones/);
  assert.doesNotMatch(app, /LocalExperimentStage|局部实验/);
  assert.doesNotMatch(app, /key="(?:real-preview|cpp-preview)"/);
});

function probeHarness(t, state) {
  const originalRaf = globalThis.requestAnimationFrame;
  const originalCancel = globalThis.cancelAnimationFrame;
  const originalMatchMedia = window.matchMedia;
  const frames = new Map();
  const styles = {};
  let nextId = 0;
  globalThis.requestAnimationFrame = callback => {
    const id = ++nextId;
    frames.set(id, callback);
    return id;
  };
  globalThis.cancelAnimationFrame = id => frames.delete(id);
  window.matchMedia = () => ({ matches: true });
  state.stageEl.value = { getBoundingClientRect: () => ({ left: 80, top: 96 }) };
  state.probeEl.value = { style: { setProperty: (name, value) => { styles[name] = value; } } };
  state.fallbackPointFromClient = () => [0, 0];
  t.after(() => {
    globalThis.requestAnimationFrame = originalRaf;
    globalThis.cancelAnimationFrame = originalCancel;
    window.matchMedia = originalMatchMedia;
  });
  const event = overrides => ({ clientX: 341.5, clientY: 278.25,
    pointerType: "mouse", buttons: 0, target: { closest: () => null }, ...overrides });
  const flush = () => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach(callback => callback());
  };
  return { frames, styles, event, flush };
}

test("selection probe uses the exact mouse pixel and coalesces moves to the latest frame", t => {
  const { state } = stage(t);
  const { frames, styles, event, flush } = probeHarness(t, state);
  state.moveProbe(event());
  state.moveProbe(event({ clientX: 360.75, clientY: 292.5 }));
  assert.equal(frames.size, 1);
  flush();
  assert.equal(styles.transform, "translate3d(280.75px, 196.5px, 0)");
  assert.equal(state.probeVisible.value, true);
  assert.equal(state.hoverInside.value, true);
});

test("Baidu probe and selected origin use the same map pixel", async t => {
  const { state, props, stats } = stage(t);
  await state.setupBaidu();
  const { event, styles, flush } = probeHarness(t, state);
  let hoveredPixel;
  state.BMap.Pixel = class { constructor(x, y) { this.x = x; this.y = y; } };
  state.map.pixelToPoint = pixel => {
    hoveredPixel = pixel;
    return new state.BMap.Point(...prepared.centerBd09);
  };
  state.map.pointToPixel = () => hoveredPixel;
  state.moveProbe(event());
  flush();
  state.handleMapClick({ point: new state.BMap.Point(...prepared.centerBd09) });
  props.candidate = stats.lastSelection;
  await nextTick();
  assert.equal(stats.selections, 1);
  assert.deepEqual(state.liveCandidate.value, { x: 261.5, y: 182.25 });
  assert.equal(styles.transform, "translate3d(261.5px, 182.25px, 0)");
});

test("leaving the map cancels the pending probe frame", t => {
  const { state } = stage(t);
  const { frames, styles, event, flush } = probeHarness(t, state);
  state.moveProbe(event());
  state.leaveProbe();
  flush();
  assert.equal(frames.size, 0);
  assert.equal(state.probeFrame, 0);
  assert.equal(state.probeVisible.value, false);
  assert.equal(styles.transform, undefined);
});

test("controls, touch input, dragging and locked selection do not show a selection probe", async t => {
  const { state, props } = stage(t);
  const { frames, event } = probeHarness(t, state);
  for (const blocked of [
    { target: { closest: () => ({}) } },
    { pointerType: "touch" }, { pointerType: "pen" }, { buttons: 1 },
  ]) {
    state.moveProbe(event());
    assert.equal(state.probeVisible.value, true);
    state.moveProbe(event(blocked));
    assert.equal(state.probeVisible.value, false);
    assert.equal(frames.size, 0);
  }
  state.moveProbe(event());
  props.selectionDisabled = true;
  await nextTick();
  assert.equal(state.probeVisible.value, false);
  assert.equal(frames.size, 0);
  state.moveProbe(event());
  assert.equal(state.probeVisible.value, false);
  props.selectionDisabled = false;
  window.matchMedia = () => ({ matches: false });
  state.moveProbe(event());
  assert.equal(state.probeVisible.value, false);
});

test("probe ring and caption are independently positioned around a zero-size mouse anchor", () => {
  const css = readFileSync(new URL("../src/realMap.css", import.meta.url), "utf8");
  const rule = selector => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const found = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
    assert.ok(found, selector);
    return found[1];
  };
  const anchor = rule(".real-map-probe");
  assert.match(anchor, /width:\s*0;/);
  assert.match(anchor, /height:\s*0;/);
  assert.match(anchor, /pointer-events:\s*none;/);
  assert.doesNotMatch(anchor, /display:\s*flex|align-items|gap:/);
  const ring = rule(".real-probe-ring");
  assert.match(ring, /position:\s*absolute;/);
  assert.match(ring, /left:\s*0;/);
  assert.match(ring, /top:\s*0;/);
  assert.match(ring, /transform:\s*translate\(-50%,\s*-50%\)/);
  assert.doesNotMatch(ring, /margin:/);
  assert.match(rule(".real-probe-ring::after"), /translate\(-50%,\s*-50%\)/);
  assert.match(rule(".real-probe-caption"), /position:\s*absolute;/);
});
