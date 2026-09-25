<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import BMapLoader from "@baidumap/jsapi-loader";
import boundaryWgs from "./data/demoBoundary.wgs84.json";
import { baiduMapStyle } from "./baiduMapStyle";
import { baiduZoomForTier, clampMapPan, zoomedFit } from "./mapZoom";
import {
  expandedLocalBounds,
  fitLocalPoints,
  geometryToSvgPath,
  localToWgs,
  pointInPolygon,
  wgsToLocal,
} from "./mapGeometry";
import "./realMap.css";

const props = defineProps({
  candidate: { type: Object, default: null },
  analysisResult: { type: Object, default: null },
  zoomTier: { type: String, default: "medium" },
});
const emit = defineEmits(["select"]);

const browserAk = import.meta.env.VITE_BAIDU_BROWSER_AK?.trim();
const boundaryWgsRing = boundaryWgs.geometry.coordinates[0];
const fallbackOrigin = [121.505, 31.333];
const fallbackRing = boundaryWgsRing.map((point) => wgsToLocal(point, fallbackOrigin));
const displayCornersLocal = expandedLocalBounds(fallbackRing);
const fallbackBoundary = geometryToSvgPath(boundaryWgs.geometry, (point) => wgsToLocal(point, fallbackOrigin));
const regionCenterWgs = [121.505, 31.333];

const stageEl = ref(null);
const baiduEl = ref(null);
const contextEl = ref(null);
const probeEl = ref(null);
const mapState = ref(browserAk ? "loading" : "no-key");
const mapError = ref("");
const notice = ref("");
const context = shallowRef(null);
const boundaryBd09 = shallowRef(null);
const displayCornersBd09 = shallowRef(null);
const regionCenterBd09 = shallowRef(null);
const viewport = ref({ width: 1, height: 1 });
const liveBoundaryPath = ref("");
const projectionVersion = ref(0);
const hoverInside = ref(true);
const probeVisible = ref(false);
const fallbackAnimated = ref(false);
const fallbackPan = ref({ x: 0, y: 0 });
const fallbackDragging = ref(false);
let map;
let BMap;
let observer;
let frame;
let probeFrame;
let noticeTimer;
let fittedZoom;
let fittedCenter;
let fallbackDrag;
let suppressFallbackClick = false;
let lastNativeDragAt = 0;
let destroyed = false;

const fallbackActive = computed(() => mapState.value !== "ready");
const fallbackFit = computed(() => fitLocalPoints(
  displayCornersLocal, viewport.value.width, viewport.value.height, 0.04,
));
const fallbackView = computed(() => {
  const fitted = zoomedFit(
    fallbackFit.value,
    viewport.value.width,
    viewport.value.height,
    props.zoomTier,
    props.candidate?.coordType === "wgs84ll"
      ? wgsToLocal([props.candidate.lng, props.candidate.lat], fallbackOrigin) : null,
  );
  return {
    ...fitted,
    translateX: fitted.translateX + fallbackPan.value.x,
    translateY: fitted.translateY + fallbackPan.value.y,
  };
});
const fallbackTransform = computed(() => {
  const { scale, translateX, translateY } = fallbackView.value;
  return `translate(${translateX}px, ${translateY}px) scale(${scale})`;
});
const contextGroups = computed(() => {
  const groups = {};
  for (const feature of context.value?.features ?? []) {
    (groups[feature.kind] ??= []).push(feature);
  }
  return groups;
});
const fallbackCandidate = computed(() => {
  if (props.candidate?.coordType !== "wgs84ll") return null;
  const [x, y] = wgsToLocal([props.candidate.lng, props.candidate.lat], fallbackOrigin);
  const { scale, translateX, translateY } = fallbackView.value;
  return { x: translateX + x * scale, y: translateY + y * scale };
});
const fallbackCandidateTransform = computed(() => fallbackCandidate.value
  ? `translate(${fallbackCandidate.value.x}px, ${fallbackCandidate.value.y}px)` : "");
const liveCandidate = computed(() => {
  projectionVersion.value;
  if (mapState.value !== "ready" || props.candidate?.coordType !== "bd09ll" || !map || !BMap) return null;
  const pixel = map.pointToPixel(new BMap.Point(props.candidate.lng, props.candidate.lat));
  return { x: pixel.x, y: pixel.y };
});
const projectedResult = computed(() => {
  projectionVersion.value;
  if (mapState.value !== "ready" || !props.analysisResult || !map || !BMap) return null;
  const project = ([lng, lat]) => {
    const pixel = map.pointToPixel(new BMap.Point(lng, lat));
    return [pixel.x, pixel.y];
  };
  const result = props.analysisResult;
  return {
    area: geometryToSvgPath(result.isochrone?.geometry, project),
    walkways: (result.reachableWalkways?.features ?? []).map((feature) =>
      geometryToSvgPath(feature.geometry, project)),
    gaps: (result.blindZoneWalkways?.features ?? []).map((feature) =>
      geometryToSvgPath(feature.geometry, project)),
  };
});
const stateMessage = computed(() => ({
  loading: "正在载入百度底图并校准范围…",
  "no-key": "百度底图待配置 · 当前展示真实道路 SVG 示意",
  error: `百度底图暂不可用 · ${mapError.value || "请检查网络或 AK"}`,
  ready: "真实底图预览 · 步行路网尚未接入",
})[mapState.value]);

function showNotice(message) {
  notice.value = message;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.value = ""; }, 3200);
}

async function loadFallbackContext() {
  if (context.value) return;
  try {
    const module = await import("./data/demoContext.extended.wgs84.json");
    if (!destroyed) context.value = module.default;
  } catch {
    showNotice("道路轮廓数据暂时无法读取，请刷新页面。");
  }
}

function scheduleProjection() {
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = 0;
    if (!map || !BMap || !boundaryBd09.value) return;
    liveBoundaryPath.value = geometryToSvgPath(
      { type: "Polygon", coordinates: [boundaryBd09.value] },
      ([lng, lat]) => {
        const pixel = map.pointToPixel(new BMap.Point(lng, lat));
        return [pixel.x, pixel.y];
      },
    );
    projectionVersion.value += 1;
  });
}

function fitBaiduViewport() {
  if (!map || !BMap || !displayCornersBd09.value) return;
  const margin = Math.max(16, Math.round(Math.min(viewport.value.width, viewport.value.height) * 0.04));
  map.setViewport(displayCornersBd09.value.map(([lng, lat]) => new BMap.Point(lng, lat)), {
    margins: [margin, margin, margin, margin],
  });
  fittedZoom = map.getZoom();
  fittedCenter = map.getCenter();
  applyBaiduZoom(false);
}

function applyBaiduZoom(animate = true) {
  if (!map || !BMap || !Number.isFinite(fittedZoom) || !fittedCenter) return;
  if (props.zoomTier === "small") map.disableDragging?.();
  else map.enableDragging?.();
  const candidate = props.candidate?.coordType === "bd09ll" ? props.candidate : null;
  const center = props.zoomTier === "large" && candidate
    ? new BMap.Point(candidate.lng, candidate.lat) : fittedCenter;
  const zoom = baiduZoomForTier(
    fittedZoom,
    props.zoomTier,
    map.getMinZoom?.() ?? 3,
    map.getMaxZoom?.() ?? 21,
  );
  if (animate && mapState.value === "ready" && map.flyTo &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    try {
      map.flyTo(center, zoom, { duration: 420, callback: scheduleProjection });
    } catch {
      map.centerAndZoom(center, zoom);
    }
  } else {
    map.centerAndZoom(center, zoom);
  }
  scheduleProjection();
}

function convertBatch(convertor, points) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("坐标转换超时")), 12000);
    convertor.translate(points, 1, 5, (result) => {
      clearTimeout(timeout);
      if (result?.status !== 0 || result.points?.length !== points.length) {
        reject(new Error(`坐标转换失败（${result?.status ?? "无响应"}）`));
      } else {
        resolve(result.points.map((point) => [point.lng, point.lat]));
      }
    });
  });
}

async function convertBoundary(namespace) {
  const convertor = new namespace.Convertor();
  const converted = [];
  // JSAPI Convertor accepts up to 10 points per call; this is done once at load.
  for (let index = 0; index < boundaryWgsRing.length; index += 10) {
    if (destroyed) return null;
    const points = boundaryWgsRing.slice(index, index + 10).map(([lng, lat]) =>
      new namespace.Point(lng, lat));
    converted.push(...await convertBatch(convertor, points));
  }
  // The first and final WGS84 vertices are identical. Keep the BD-09 ring exact.
  converted[converted.length - 1] = [...converted[0]];
  regionCenterBd09.value = (await convertBatch(convertor, [
    new namespace.Point(...regionCenterWgs),
  ]))[0];
  displayCornersBd09.value = await convertBatch(convertor, displayCornersLocal.map((point) =>
    new namespace.Point(...localToWgs(point, fallbackOrigin))));
  return converted;
}

function selectPoint(lng, lat, coordType) {
  const ring = coordType === "bd09ll" ? boundaryBd09.value : boundaryWgsRing;
  if (!ring || !pointInPolygon([lng, lat], [ring])) {
    showNotice("请在国帆路、江湾城路、殷高东路、国权北路围合区内选点。");
    return;
  }
  emit("select", { lng, lat, coordType });
  showNotice("候选起点已记录；真实路网接入后才能计算步行结果。");
}

function handleMapClick(event) {
  if (performance.now() - lastNativeDragAt < 260) return;
  if (event.point) selectPoint(event.point.lng, event.point.lat, "bd09ll");
}

function handleFallbackClick(event) {
  if (!fallbackActive.value || suppressFallbackClick ||
    event.target.closest("button, a, .real-map-actions, .real-map-note, .real-map-toast")) return;
  const local = fallbackPointFromClient(event.clientX, event.clientY);
  if (!local) return;
  const [lng, lat] = localToWgs(local, fallbackOrigin);
  selectPoint(lng, lat, "wgs84ll");
}

function fallbackPointFromClient(clientX, clientY) {
  const matrix = contextEl.value?.getScreenCTM();
  if (!matrix) return null;
  const point = new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse());
  return [point.x, point.y];
}

function beginFallbackDrag(event) {
  if (!fallbackActive.value || props.zoomTier === "small" ||
    (event.pointerType === "mouse" && event.button !== 0) ||
    event.target.closest("button, a, .real-map-actions, .real-map-note, .real-map-toast")) return;
  contextEl.value?.getAnimations().forEach((animation) => animation.finish());
  fallbackDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    pan: { ...fallbackPan.value },
  };
}

function moveFallbackDrag(event) {
  if (!fallbackDrag || fallbackDrag.pointerId !== event.pointerId) return;
  const dx = event.clientX - fallbackDrag.startX;
  const dy = event.clientY - fallbackDrag.startY;
  if (!fallbackDragging.value && Math.hypot(dx, dy) < 6) return;
  if (!fallbackDragging.value) {
    fallbackDragging.value = true;
    suppressFallbackClick = true;
    stageEl.value?.setPointerCapture(event.pointerId);
    probeVisible.value = false;
  }
  const limit = props.zoomTier === "large" ? 0.8 : 0.26;
  fallbackPan.value = {
    x: clampMapPan(fallbackDrag.pan.x + dx, viewport.value.width * limit),
    y: clampMapPan(fallbackDrag.pan.y + dy, viewport.value.height * limit),
  };
}

function endFallbackDrag(event) {
  if (event && fallbackDrag?.pointerId !== event.pointerId) return;
  if (event && stageEl.value?.hasPointerCapture?.(event.pointerId)) {
    stageEl.value.releasePointerCapture(event.pointerId);
  }
  if (fallbackDragging.value) setTimeout(() => { suppressFallbackClick = false; }, 0);
  fallbackDrag = null;
  fallbackDragging.value = false;
}

function handlePointerMove(event) {
  moveFallbackDrag(event);
  if (!fallbackDragging.value) moveProbe(event);
}

function selectRegionCenter() {
  if (mapState.value === "ready" && regionCenterBd09.value) {
    selectPoint(regionCenterBd09.value[0], regionCenterBd09.value[1], "bd09ll");
  } else {
    selectPoint(regionCenterWgs[0], regionCenterWgs[1], "wgs84ll");
  }
}

function moveProbe(event) {
  if (!stageEl.value || !probeEl.value || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  const bounds = stageEl.value.getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  if (probeFrame) cancelAnimationFrame(probeFrame);
  probeFrame = requestAnimationFrame(() => {
    probeEl.value?.style.setProperty("transform", `translate3d(${x}px, ${y}px, 0)`);
    probeFrame = 0;
  });
  probeVisible.value = true;
  if (mapState.value === "ready" && map && BMap && boundaryBd09.value) {
    const point = map.pixelToPoint(new BMap.Pixel(x, y));
    hoverInside.value = pointInPolygon([point.lng, point.lat], [boundaryBd09.value]);
  } else {
    const local = fallbackPointFromClient(event.clientX, event.clientY);
    if (local) hoverInside.value = pointInPolygon(localToWgs(local, fallbackOrigin), [boundaryWgsRing]);
  }
}

function leaveProbe() {
  probeVisible.value = false;
  if (probeFrame) cancelAnimationFrame(probeFrame);
}

async function setupBaidu() {
  try {
    BMap = await BMapLoader.load({ ak: browserAk, version: "4.0", timeout: 18000 });
    if (destroyed) return;
    boundaryBd09.value = await convertBoundary(BMap);
    if (destroyed || !boundaryBd09.value) return;
    await nextTick();
    map = new BMap.Map(baiduEl.value, { enableMapClick: false });
    map.centerAndZoom(new BMap.Point(...boundaryBd09.value[0]), 14);
    map.disableDragging?.();
    map.disableScrollWheelZoom?.();
    map.disableDoubleClickZoom?.();
    map.disableKeyboard?.();
    map.disablePinchToZoom?.();
    try {
      map.setMapStyle?.({ styleJson: baiduMapStyle });
    } catch (error) {
      console.warn("百度底图样式不可用，继续显示默认底图。", error);
    }
    map.addEventListener("click", handleMapClick);
    map.addEventListener("dragend", handleNativeDragEnd);
    map.addEventListener("moving", scheduleProjection);
    map.addEventListener("zooming", scheduleProjection);
    map.addEventListener("moveend", scheduleProjection);
    map.addEventListener("zoomend", scheduleProjection);
    fitBaiduViewport();
    mapState.value = "ready";
    scheduleProjection();
  } catch (error) {
    if (destroyed) return;
    console.warn("百度地图加载或坐标转换失败，已切换到 SVG 预览。", error);
    mapError.value = error instanceof Error && error.message.startsWith("坐标转换")
      ? error.message : "请检查网络、AK 和域名白名单";
    mapState.value = "error";
    await loadFallbackContext();
  }
}

function updateSize() {
  if (!stageEl.value) return;
  viewport.value = {
    width: Math.max(1, stageEl.value.clientWidth),
    height: Math.max(1, stageEl.value.clientHeight),
  };
  if (mapState.value === "ready") fitBaiduViewport();
}

function handleNativeDragEnd() {
  lastNativeDragAt = performance.now();
  scheduleProjection();
}

watch(() => props.zoomTier, () => {
  fallbackPan.value = { x: 0, y: 0 };
  if (mapState.value === "ready") applyBaiduZoom();
});
watch(() => props.candidate, () => {
  if (props.zoomTier === "large") {
    fallbackPan.value = { x: 0, y: 0 };
    if (mapState.value === "ready") applyBaiduZoom();
  } else if (mapState.value === "ready") scheduleProjection();
});
onMounted(() => {
  updateSize();
  observer = new ResizeObserver(updateSize);
  observer.observe(stageEl.value);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!destroyed) fallbackAnimated.value = true;
  }));
  if (browserAk) setupBaidu();
  else loadFallbackContext();
});
onUnmounted(() => {
  destroyed = true;
  observer?.disconnect();
  clearTimeout(noticeTimer);
  if (frame) cancelAnimationFrame(frame);
  if (probeFrame) cancelAnimationFrame(probeFrame);
  if (map) {
    map.removeEventListener("click", handleMapClick);
    map.removeEventListener("dragend", handleNativeDragEnd);
    map.removeEventListener("moving", scheduleProjection);
    map.removeEventListener("zooming", scheduleProjection);
    map.removeEventListener("moveend", scheduleProjection);
    map.removeEventListener("zoomend", scheduleProjection);
    map.destroy?.();
  }
});
</script>

<template>
  <div
    ref="stageEl"
    class="real-map-stage"
    :class="{ 'is-outside': !hoverInside, 'can-pan': zoomTier !== 'small', 'is-panning': fallbackDragging, 'can-animate': fallbackAnimated }"
    role="group"
    aria-label="四路围合演示区域地图，可点击区域内位置设置候选起点"
    @click="handleFallbackClick"
    @pointerdown="beginFallbackDrag"
    @pointermove="handlePointerMove"
    @pointerup="endFallbackDrag"
    @pointercancel="endFallbackDrag"
    @lostpointercapture="endFallbackDrag"
    @pointerleave="leaveProbe"
  >
    <div ref="baiduEl" class="real-baidu-map" :class="{ visible: mapState === 'ready' }" aria-hidden="true"></div>

    <svg
      v-if="fallbackActive"
      class="real-fallback-svg"
      :viewBox="`0 0 ${viewport.width} ${viewport.height}`"
      preserveAspectRatio="none"
      role="img"
      aria-label="基于公开道路资料的演示区 SVG 轮廓示意"
    >
      <defs>
        <pattern id="real-map-grid" width="30" height="30" patternUnits="userSpaceOnUse">
          <path d="M 30 0 L 0 0 0 30" fill="none" stroke="#dce8e1" stroke-width=".65" />
        </pattern>
      </defs>
      <rect :width="viewport.width" :height="viewport.height" fill="#eaf0ec" />
      <rect :width="viewport.width" :height="viewport.height" fill="url(#real-map-grid)" opacity=".45" />
      <g ref="contextEl" :style="{ transform: fallbackTransform }" class="real-context">
        <path v-for="feature in contextGroups.waterArea" :key="feature.id" :d="feature.d" class="real-water-area" />
        <path v-for="feature in contextGroups.park" :key="feature.id" :d="feature.d" class="real-park-area" />
        <path v-for="feature in contextGroups.building" :key="feature.id" :d="feature.d" class="real-building" />
        <path v-for="feature in contextGroups.waterLine" :key="feature.id" :d="feature.d" class="real-water-line" />
        <path v-for="feature in contextGroups.roadMajor" :key="`${feature.id}-base`" :d="feature.d" class="real-road-major-base" />
        <path v-for="feature in contextGroups.roadLocal" :key="`${feature.id}-base`" :d="feature.d" class="real-road-local-base" />
        <path v-for="feature in contextGroups.roadMajor" :key="feature.id" :d="feature.d" class="real-road-major" />
        <path v-for="feature in contextGroups.roadLocal" :key="feature.id" :d="feature.d" class="real-road-local" />
        <path v-for="feature in contextGroups.roadPath" :key="feature.id" :d="feature.d" class="real-road-path" />
        <path :d="fallbackBoundary" class="real-boundary-fill" />
        <path :d="fallbackBoundary" class="real-boundary-outline" />
      </g>
      <g v-if="fallbackCandidate" :style="{ transform: fallbackCandidateTransform }" class="real-candidate-mark" aria-hidden="true">
        <circle r="12" class="real-candidate-halo" /><circle r="6" class="real-candidate-dot" />
      </g>
    </svg>

    <svg
      v-else
      class="real-live-overlay"
      :viewBox="`0 0 ${viewport.width} ${viewport.height}`"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path v-if="projectedResult?.area" :d="projectedResult.area" class="real-analysis-area" fill-rule="evenodd" />
      <path v-for="(path, index) in projectedResult?.walkways ?? []" :key="`walk-${index}`" :d="path" class="real-analysis-walkway" />
      <path v-for="(path, index) in projectedResult?.gaps ?? []" :key="`gap-${index}`" :d="path" class="real-analysis-gap" />
      <path :d="liveBoundaryPath" class="real-live-boundary-fill" fill-rule="evenodd" />
      <path :d="liveBoundaryPath" class="real-live-boundary-outline" />
      <g v-if="liveCandidate" :transform="`translate(${liveCandidate.x} ${liveCandidate.y})`" class="real-candidate-mark">
        <circle r="12" class="real-candidate-halo" /><circle r="6" class="real-candidate-dot" />
      </g>
    </svg>

    <div class="real-map-note">
      <span class="real-note-signal" aria-hidden="true"></span>
      <span>{{ stateMessage }}</span>
    </div>
    <div class="real-map-actions">
      <p>虚线内可选起点，外围仅用于展示<br /><strong>点击区内位置，记录候选起点</strong></p>
      <button type="button" @click.stop="selectRegionCenter">选区域中心</button>
    </div>
    <div ref="probeEl" class="real-map-probe" :class="{ visible: probeVisible, outside: !hoverInside }" aria-hidden="true">
      <span></span><small>{{ hoverInside ? "选起点" : "区外" }}</small>
    </div>
    <p v-if="notice" class="real-map-toast" role="status">{{ notice }}</p>
  </div>
</template>
