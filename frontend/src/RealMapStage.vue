<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import BMapLoader from "@baidumap/jsapi-loader";
import boundaryWgs from "./data/demoBoundary.wgs84.json";
import preparedMapAsset from "./data/demoMap.bd09.json";
import { preparedMapCenter, preparedMapGeometry, sampleLocalArea, sampleLocalPath } from "./mapAsset.js";
import { baiduMapStyle } from "./baiduMapStyle";
import { baiduZoomForTier, clampMapPan, markerScaleForTier, zoomedFit } from "./mapZoom";
import {
  DISPLAY_PADDING_METERS,
  expandedLocalBounds,
  fitLocalPoints,
  geometryToSvgPath,
  localToWgs,
  pointInPolygon,
  wgsToLocal,
} from "./mapGeometry";
import "./realMap.css";
import PoiInventoryPanel from "./PoiInventoryPanel.vue";
import { poiAccessLabel, poiCategoryStyles, visiblePois } from "./poiFacilities.js";
import { cppOverviewFit, cppOverviewInsets, cppOverviewPoints } from "./cppOverview.js";

const props = defineProps({
  candidate: { type: Object, default: null },
  analysisResult: { type: Object, default: null },
  zoomTier: { type: String, default: "medium" },
  overviewRequestId: { type: Number, default: 0 },
  analysisMode: { type: String, default: "preview" },
  selectionDisabled: { type: Boolean, default: false },
  showBlindZones: { type: Boolean, default: false },
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
const boundaryState = ref("pending");
const boundaryError = ref("");
const notice = ref("");
const context = shallowRef(null);
const boundaryBd09 = shallowRef(null);
const displayCornersBd09 = shallowRef(null);
const regionCenterBd09 = shallowRef(null);
const mapAlignment = shallowRef(null);
const viewport = ref({ width: 1, height: 1 });
const liveBoundaryPath = ref("");
const projectionVersion = ref(0);
const hoverInside = ref(true);
const probeVisible = ref(false);
const fallbackAnimated = ref(false);
const fallbackPan = ref({ x: 0, y: 0 });
// Freeze the camera's overview input until the user explicitly requests a
// refit. Clearing/replacing analysis layers must not move the map.
const overviewResult = shallowRef(props.zoomTier === "result" ? props.analysisResult : null);
const fallbackDragging = ref(false);
const poiCategory = ref("all");
const selectedPoi = ref(null);
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
let mapSetupPromise;

const fallbackActive = computed(() => mapState.value !== "ready");
const candidateMarkerScale = computed(() => markerScaleForTier(props.zoomTier));
const fallbackFit = computed(() => fitLocalPoints(
  displayCornersLocal, viewport.value.width, viewport.value.height, 0.04,
));
const fallbackView = computed(() => {
  const overview = props.zoomTier === "result" && props.analysisMode === "cpp"
    ? cppOverviewFit(overviewResult.value, viewport.value.width, viewport.value.height) : null;
  const fitted = overview ?? zoomedFit(
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
  const pixel = mapPointToOverlayPixel([props.candidate.lng, props.candidate.lat]);
  return { x: pixel.x, y: pixel.y };
});
function localToBd09(point) {
  return mapAlignment.value?.toBd09(point) ?? null;
}

function bd09ToLocal(point) {
  return mapAlignment.value?.toLocal(point) ?? null;
}

// The route/area SVG is an external overlay. Baidu exposes a separate pixel
// origin for that container; using pointToPixel here shifts the whole layer
// when the map viewport and overlay panes are not identical. Keep the
// pointToPixel fallback for older SDK shims and unit-test doubles.
function mapPointToOverlayPixel(point) {
  if (!map || !BMap) return { x: 0, y: 0 };
  const projector = map.pointToOverlayPixel ?? map.pointToPixel;
  const pixel = projector.call(map, new BMap.Point(point[0], point[1]));
  return { x: pixel.x, y: pixel.y };
}

function linePath(points) {
  return points.map((point, index) => `${index ? "L" : "M"} ${point[0]} ${point[1]}`).join(" ");
}

function circlePath(center, radius, project) {
  const points = Array.from({ length: 65 }, (_, index) => {
    const angle = index * Math.PI * 2 / 64;
    return project([center.x + radius * Math.cos(angle), center.y + radius * Math.sin(angle)]);
  });
  return `M ${points.map((point) => point.join(" ")).join(" L ")} Z`;
}

function routeDelay(segment, result) {
  const progress = segment.startProgress ?? (segment.startDistance ?? 0) /
    (result.animationMaxDistance ?? result.displayArea?.radius ?? 900);
  return `${0.45 + Math.max(0, Math.min(1, progress)) * 2.05}s`;
}

function localAreaPath(result) {
  return geometryToSvgPath(result.displayArea.geometry, (point) => point);
}

const fallbackDemoResult = computed(() => props.analysisResult?.coordinateSystem === "preview-local-v1"
  ? props.analysisResult : null);
const cppSyntheticResult = computed(() => props.analysisResult?.source === "synthetic-cpp-engine");
const liveDemoResult = computed(() => {
  projectionVersion.value;
  const result = props.analysisResult;
  if (props.analysisMode === "cpp" && result?.displayArea?.type !== "polygon") return null;
  if (mapState.value !== "ready" ||
    !["preview-local-v1", "bd09ll"].includes(result?.coordinateSystem) ||
    !map || !BMap || !displayCornersBd09.value) return null;
  // Both directions use the same precomputed official calibration grid.
  const project = (point) => {
    const converted = result.coordinateSystem === "preview-local-v1" ? localToBd09(point) : point;
    const pixel = mapPointToOverlayPixel(converted);
    return [pixel.x, pixel.y];
  };
  const projectPath = points => (result.coordinateSystem === "preview-local-v1"
    ? sampleLocalPath(points) : points).map(project);
  return {
    area: result.displayArea.type === "circle"
      ? circlePath(result.displayArea.center, result.displayArea.radius, project)
      : geometryToSvgPath(result.coordinateSystem === "preview-local-v1"
        ? sampleLocalArea(result.displayArea.geometry) : result.displayArea.geometry, project),
    routes: (props.analysisMode === "cpp" ? result.routeSegments ?? [] : []).map((segment) => ({
      id: segment.id,
      d: linePath(projectPath(segment.points)),
      delay: routeDelay(segment, result),
    })),
    poiRoutes: (props.analysisMode === "preview" ? result.routeSegments ?? [] : []).map((segment) => ({
      id: segment.id,
      d: linePath(projectPath(segment.points)),
      delay: routeDelay(segment, result),
    })),
    samplingRoutes: (props.analysisMode === "preview" ? result.samplingRouteSegments ?? [] : []).map((segment) => ({
      id: segment.id,
      d: linePath(projectPath(segment.points)),
      delay: routeDelay(segment, result),
    })),
    access: result.accessLink ? linePath(projectPath(result.accessLink.points)) : "",
  };
});
const projectedResult = computed(() => {
  projectionVersion.value;
  if (mapState.value !== "ready" || !props.analysisResult || !map || !BMap) return null;
  const project = ([lng, lat]) => {
    const pixel = mapPointToOverlayPixel([lng, lat]);
    return [pixel.x, pixel.y];
  };
  const result = props.analysisResult;
  const projectGeometry = (geometry) => {
    if (!geometry) return "";
    const projectPoint = result.coordinateSystem === "preview-local-v1"
      ? (point) => project(localToBd09(point)) : project;
    return geometryToSvgPath(geometry, projectPoint);
  };
  return {
    area: projectGeometry(result.isochrone?.geometry),
    walkways: (result.reachableWalkways?.features ?? []).map((feature) =>
      projectGeometry(feature.geometry)),
    gaps: (result.blindZoneWalkways?.features ?? []).map((feature) =>
      projectGeometry(feature.geometry)),
    blindZones: (props.showBlindZones ? result.blindZones?.features ?? [] : []).map((feature) =>
      projectGeometry(feature.geometry)),
  };
});
const poiMarkers = computed(() => {
  projectionVersion.value;
  return visiblePois(props.analysisResult, poiCategory.value).flatMap((poi) => {
    if (mapState.value === "ready" && map && BMap && poi.bd09) {
      const pixel = mapPointToOverlayPixel(poi.bd09);
      return [{ ...poi, pixel: [pixel.x, pixel.y] }];
    }
    if (!poi.point) return [];
    const { scale, translateX, translateY } = fallbackView.value;
    return [{ ...poi, pixel: [translateX + poi.point[0] * scale, translateY + poi.point[1] * scale] }];
  });
});
const stateMessage = computed(() => props.analysisMode === "cpp" ? ({
  loading: "正在载入百度底图 · 使用本地边界数据…",
  "no-key": "专家模式 · SVG 底图与 C++ 合成路网",
  error: `百度底图暂不可用 · ${mapError.value || "请检查网络或 AK"}`,
  ready: boundaryState.value === "ready" ? "专家模式 · C++ 合成路网等时圈，未实地核实"
    : `底图已载入 · 选区暂不可用：${boundaryError.value}`,
})[mapState.value] : ({
  loading: "正在载入百度底图 · 使用本地边界数据…",
  "no-key": "真实区域 · SVG 底图与采样路线预览",
  error: `百度底图暂不可用 · ${mapError.value || "请检查网络或 AK"}`,
  ready: boundaryState.value === "ready" ? "真实区域 · 百度采样等时圈与步行路线"
    : `底图已载入 · 选区暂不可用：${boundaryError.value}`,
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
    if (!map || !BMap) return;
    liveBoundaryPath.value = boundaryBd09.value ? geometryToSvgPath(
      { type: "Polygon", coordinates: [boundaryBd09.value] },
      ([lng, lat]) => {
        const pixel = mapPointToOverlayPixel([lng, lat]);
        return [pixel.x, pixel.y];
      },
    ) : "";
    projectionVersion.value += 1;
  });
}

function fitBaiduViewport(animate = false) {
  if (!map || !BMap || !displayCornersBd09.value) return;
  const margin = Math.max(16, Math.round(Math.min(viewport.value.width, viewport.value.height) * 0.04));
  const overview = props.zoomTier === "result" && props.analysisMode === "cpp"
    ? cppOverviewPoints(overviewResult.value) : [];
  const points = overview.length ? [
    ...overview.map(localToBd09), ...visiblePois(overviewResult.value).map(poi => poi.bd09),
  ].filter(point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite))
    : displayCornersBd09.value;
  const inset = cppOverviewInsets(viewport.value.width, viewport.value.height);
  // Calculate only: setViewport schedules a camera change, so immediately
  // reading getZoom/getCenter after it captures the previous (already scaled)
  // view. Reusing that value makes each tier switch multiply the zoom again.
  const fitted = map.getViewport(points.map(([lng, lat]) => new BMap.Point(lng, lat)), {
    margins: overview.length ? [inset.top, inset.right, inset.bottom, inset.left]
      : [margin, margin, margin, margin],
  });
  if (!Number.isFinite(fitted?.zoom) || !fitted.center) return;
  fittedZoom = fitted.zoom;
  fittedCenter = fitted.center;
  // One absolute camera target, with no preceding queued viewport mutation.
  applyBaiduZoom(animate);
}

function applyBaiduZoom(animate = true) {
  if (!map || !BMap || !Number.isFinite(fittedZoom) || !fittedCenter) return;
  if (["small", "result"].includes(props.zoomTier)) map.disableDragging?.();
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
      map.centerAndZoom(center, zoom, { noAnimation: true, callback: scheduleProjection });
    }
  } else {
    map.centerAndZoom(center, zoom, { noAnimation: true, callback: scheduleProjection });
  }
  scheduleProjection();
}

function loadPreparedBoundary() {
  try {
    const { ring, corners, alignment } = preparedMapGeometry(preparedMapAsset, {
      ringWgs84: boundaryWgsRing,
      originWgs84: fallbackOrigin,
      paddingMeters: DISPLAY_PADDING_METERS,
    });
    boundaryBd09.value = ring;
    displayCornersBd09.value = corners;
    mapAlignment.value = alignment;
    boundaryState.value = "ready";
    boundaryError.value = "";
  } catch (error) {
    boundaryBd09.value = null;
    displayCornersBd09.value = null;
    mapAlignment.value = null;
    boundaryState.value = "error";
    boundaryError.value = error.message;
  }
}

function selectPoint(lng, lat, coordType, localPoint = null) {
  if (props.selectionDisabled) {
    showNotice("当前分析尚未完成，请稍候再选点。");
    return;
  }
  const ring = coordType === "bd09ll" ? boundaryBd09.value : boundaryWgsRing;
  if (coordType === "bd09ll" && boundaryState.value !== "ready") {
    showNotice("选区数据尚未准备好，暂不能在百度底图上选点。");
    return;
  }
  if (!ring || !pointInPolygon([lng, lat], [ring])) {
    showNotice("请在国帆路、江湾城路、殷高东路、国权北路围合区内选点。");
    return;
  }
  const local = localPoint ?? (coordType === "bd09ll"
    ? bd09ToLocal([lng, lat]) : wgsToLocal([lng, lat], fallbackOrigin));
  if (!local) {
    showNotice("坐标尚未准备好，请稍后再选点。");
    return;
  }
  emit("select", { lng, lat, coordType, local: { x: local[0], y: local[1] } });
  showNotice(props.analysisMode === "cpp"
    ? "起点已选；点击右上角运行 C++ 路网计算。"
    : "起点已选；点击右上角生成百度采样等时圈与代表路线。");
}

function handleMapClick(event) {
  if (performance.now() - lastNativeDragAt < 260) return;
  if (event.point) selectPoint(event.point.lng, event.point.lat, "bd09ll");
}

function handleFallbackClick(event) {
  if (!fallbackActive.value || suppressFallbackClick ||
    event.target.closest("button, a, .real-map-actions, .real-map-note, .real-map-toast, .real-poi-marker, .real-poi-panel, .real-poi-popover")) return;
  const local = fallbackPointFromClient(event.clientX, event.clientY);
  if (!local) return;
  const [lng, lat] = localToWgs(local, fallbackOrigin);
  selectPoint(lng, lat, "wgs84ll", local);
}

function fallbackPointFromClient(clientX, clientY) {
  const matrix = contextEl.value?.getScreenCTM();
  if (!matrix) return null;
  const point = new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse());
  return [point.x, point.y];
}

function beginFallbackDrag(event) {
  if (!fallbackActive.value || ["small", "result"].includes(props.zoomTier) ||
    (event.pointerType === "mouse" && event.button !== 0) ||
    event.target.closest("button, a, .real-map-actions, .real-map-note, .real-map-toast, .real-poi-marker, .real-poi-panel, .real-poi-popover")) return;
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
    leaveProbe();
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
  if (mapState.value === "ready") {
    if (boundaryState.value !== "ready" || !regionCenterBd09.value) {
      showNotice("选区数据尚未准备好，暂不能在百度底图上选点。");
      return;
    }
    selectPoint(regionCenterBd09.value[0], regionCenterBd09.value[1], "bd09ll");
  } else {
    selectPoint(regionCenterWgs[0], regionCenterWgs[1], "wgs84ll");
  }
}

function moveProbe(event) {
  if (!stageEl.value || !probeEl.value || props.selectionDisabled ||
    (event.pointerType && event.pointerType !== "mouse") || event.buttons > 0 ||
    !window.matchMedia("(hover: hover) and (pointer: fine)").matches ||
    event.target?.closest("button, a, .real-map-actions, .real-map-note, .real-map-toast, .real-poi-marker, .real-poi-panel, .real-poi-popover")) {
    leaveProbe();
    return;
  }
  const bounds = stageEl.value.getBoundingClientRect();
  // This is the ring's centre, not the top-left of a ring/caption flex row.
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  if (probeFrame) cancelAnimationFrame(probeFrame);
  probeFrame = requestAnimationFrame(() => {
    probeEl.value?.style.setProperty("transform", `translate3d(${x}px, ${y}px, 0)`);
    probeFrame = 0;
  });
  probeVisible.value = true;
  if (mapState.value === "ready" && map && BMap) {
    if (!boundaryBd09.value) {
      hoverInside.value = false;
      return;
    }
    const point = (map.overlayPixelToPoint ?? map.pixelToPoint).call(
      map,
      new BMap.Pixel(x, y),
    );
    hoverInside.value = pointInPolygon([point.lng, point.lat], [boundaryBd09.value]);
  } else {
    const local = fallbackPointFromClient(event.clientX, event.clientY);
    if (local) hoverInside.value = pointInPolygon(localToWgs(local, fallbackOrigin), [boundaryWgsRing]);
  }
}

function leaveProbe() {
  probeVisible.value = false;
  if (probeFrame) cancelAnimationFrame(probeFrame);
  probeFrame = 0;
}

function setupBaidu() {
  // Share an in-flight initialization too, rather than launching another SDK.
  mapSetupPromise ??= initializeBaidu();
  return mapSetupPromise;
}

async function initializeBaidu() {
  // Overlay validation must not prevent a valid base map from opening.
  loadPreparedBoundary();
  regionCenterBd09.value = preparedMapCenter(preparedMapAsset);
  if (!regionCenterBd09.value) {
    mapError.value = "本地 BD-09 地图中心缺失，请重新生成地图数据";
    mapState.value = "error";
    await loadFallbackContext();
    return;
  }
  try {
    BMap = await BMapLoader.load({ ak: browserAk, version: "4.0", timeout: 18000 });
    if (destroyed) return;
    await nextTick();
    if (destroyed || !baiduEl.value) return;
    map = new BMap.Map(baiduEl.value, { enableMapClick: false });
    map.centerAndZoom(new BMap.Point(...regionCenterBd09.value), 14);
    fittedZoom = map.getZoom();
    fittedCenter = map.getCenter();
    map.disableDragging?.();
    // The page shell also listens for wheel events to turn pages. The root
    // stage stops propagation (see template below), while Baidu handles the
    // wheel itself for continuous map zooming.
    map.enableScrollWheelZoom?.();
    map.disableDoubleClickZoom?.();
    map.disableKeyboard?.();
    map.disablePinchToZoom?.();
    try {
      map.setMapStyle?.({ styleJson: baiduMapStyle });
    } catch {
      console.warn("百度底图样式不可用，继续显示默认底图。");
    }
    map.addEventListener("click", handleMapClick);
    map.addEventListener("dragend", handleNativeDragEnd);
    map.addEventListener("moving", scheduleProjection);
    map.addEventListener("zooming", scheduleProjection);
    map.addEventListener("moveend", scheduleProjection);
    map.addEventListener("zoomend", scheduleProjection);
    fitBaiduViewport();
    if (boundaryState.value !== "ready") applyBaiduZoom(false);
    mapState.value = "ready";
    scheduleProjection();
  } catch {
    if (destroyed) return;
    console.warn("百度底图初始化失败，已切换到 SVG 预览；边界不调用在线转换。");
    mapError.value = "请检查网络、浏览器 AK、服务权限和域名白名单";
    mapState.value = "error";
    await loadFallbackContext();
  }
}

function updateSize() {
  // Do not refit/reload tiles when the retained map is hidden in local mode.
  if (!stageEl.value?.clientWidth || !stageEl.value.clientHeight) return;
  if (stageEl.value.clientWidth === viewport.value.width &&
    stageEl.value.clientHeight === viewport.value.height) return;
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

watch([() => props.zoomTier, () => props.overviewRequestId], () => {
  overviewResult.value = props.zoomTier === "result" ? props.analysisResult : null;
  fallbackPan.value = { x: 0, y: 0 };
  if (mapState.value === "ready") fitBaiduViewport(true);
});
watch(() => props.candidate, () => {
  selectedPoi.value = null;
  if (props.zoomTier === "large") {
    fallbackPan.value = { x: 0, y: 0 };
    if (mapState.value === "ready") applyBaiduZoom();
  } else if (mapState.value === "ready") scheduleProjection();
});
watch(() => props.analysisResult, () => {
  selectedPoi.value = null;
  if (mapState.value === "ready") scheduleProjection();
});
watch(() => props.analysisMode, () => {
  selectedPoi.value = null;
  poiCategory.value = "all";
  notice.value = "";
  leaveProbe();
  overviewResult.value = props.zoomTier === "result" ? props.analysisResult : null;
  if (mapState.value === "ready") scheduleProjection();
});
watch(() => props.selectionDisabled, (disabled) => {
  if (disabled) leaveProbe();
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
    :class="{ 'is-outside': !hoverInside, 'can-pan': !['small', 'result'].includes(zoomTier), 'is-panning': fallbackDragging, 'can-animate': fallbackAnimated, 'has-cpp-result': cppSyntheticResult }"
    role="group"
    aria-label="四路围合演示区域地图，可点击区域内位置设置候选起点"
    @click="handleFallbackClick"
    @pointerdown="beginFallbackDrag"
    @pointermove="handlePointerMove"
    @pointerup="endFallbackDrag"
    @pointercancel="endFallbackDrag"
    @lostpointercapture="endFallbackDrag"
    @pointerleave="leaveProbe"
    @wheel.stop
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
        <g v-if="fallbackDemoResult" class="real-demo-result" aria-hidden="true">
          <circle v-if="analysisMode !== 'cpp' && fallbackDemoResult.displayArea.type === 'circle'" :cx="fallbackDemoResult.displayArea.center.x" :cy="fallbackDemoResult.displayArea.center.y" :r="fallbackDemoResult.displayArea.radius" class="real-demo-area" />
          <path v-else-if="fallbackDemoResult.displayArea.type === 'polygon'" :d="localAreaPath(fallbackDemoResult)" class="real-demo-area" fill-rule="evenodd" />
          <path v-for="segment in fallbackDemoResult.routeSegments" :key="segment.id" :d="linePath(segment.points)" class="real-demo-route" pathLength="1" :style="{ '--route-delay': routeDelay(segment, fallbackDemoResult) }" />
          <path v-if="fallbackDemoResult.accessLink?.length > 2" :d="linePath(fallbackDemoResult.accessLink.points)" class="real-demo-access" />
        </g>
      </g>
      <g v-if="fallbackCandidate" :style="{ transform: fallbackCandidateTransform }" class="real-candidate-mark" aria-hidden="true">
        <g class="real-candidate-glyph" :style="{ transform: `scale(${candidateMarkerScale})` }">
          <circle r="8" class="real-candidate-halo" /><circle r="4" class="real-candidate-dot" />
        </g>
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
      <path v-for="(path, index) in projectedResult?.blindZones ?? []" :key="`blind-${index}`" :d="path" class="real-blind-zone" fill-rule="evenodd" />
      <path :d="liveBoundaryPath" class="real-live-boundary-fill" fill-rule="evenodd" />
      <path :d="liveBoundaryPath" class="real-live-boundary-outline" />
      <g v-if="liveDemoResult" class="real-demo-result">
        <path :d="liveDemoResult.area" class="real-demo-area" />
        <path v-for="segment in liveDemoResult.routes" :key="segment.id" :d="segment.d" class="real-demo-route" pathLength="1" :style="{ '--route-delay': segment.delay }" />
        <path v-for="segment in liveDemoResult.samplingRoutes" :key="`sample-${segment.id}`" :d="segment.d" class="real-sampled-route" pathLength="1" :style="{ '--route-delay': segment.delay }" />
        <path v-for="segment in liveDemoResult.poiRoutes" :key="`poi-${segment.id}`" :d="segment.d" class="real-poi-route" pathLength="1" :style="{ '--route-delay': segment.delay }" />
        <path v-if="analysisResult.accessLink?.length > 2" :d="liveDemoResult.access" class="real-demo-access" />
      </g>
      <g v-if="liveCandidate" :transform="`translate(${liveCandidate.x} ${liveCandidate.y})`" class="real-candidate-mark">
        <g class="real-candidate-glyph" :style="{ transform: `scale(${candidateMarkerScale})` }">
          <circle r="8" class="real-candidate-halo" /><circle r="4" class="real-candidate-dot" />
        </g>
      </g>
    </svg>

    <svg v-if="analysisMode === 'cpp' && analysisResult" class="real-poi-layer"
      :viewBox="`0 0 ${viewport.width} ${viewport.height}`" role="group" aria-label="等时圈内基础设施候选点位">
      <g v-for="poi in poiMarkers" :key="poi.id" class="real-poi-marker"
        :transform="`translate(${poi.pixel[0]} ${poi.pixel[1]})`" tabindex="0" role="button"
        :aria-label="`${poiCategoryStyles[poi.category].label}：${poi.name}`"
        @pointerdown.stop @click.stop="selectedPoi = poi" @keydown.enter.stop="selectedPoi = poi" @keydown.space.prevent.stop="selectedPoi = poi">
        <title>{{ poi.name }} · {{ poiAccessLabel(poi) }}</title>
        <circle r="11" :stroke="poiCategoryStyles[poi.category].color" />
        <text text-anchor="middle" dominant-baseline="central" :fill="poiCategoryStyles[poi.category].color">{{ poiCategoryStyles[poi.category].glyph }}</text>
      </g>
    </svg>
    <PoiInventoryPanel v-if="analysisMode === 'cpp' && analysisResult" class="real-poi-panel"
      compact :result="analysisResult" :category="poiCategory" @category="poiCategory = $event; selectedPoi = null" />
    <div v-if="selectedPoi" class="real-poi-popover" role="status" @pointerdown.stop @click.stop>
      <button type="button" aria-label="关闭设施详情" @click.stop="selectedPoi = null">×</button>
      <strong>{{ selectedPoi.name }}</strong>
      <span>{{ poiCategoryStyles[selectedPoi.category].label }} · 百度 POI</span>
      <small>{{ poiAccessLabel(selectedPoi) }}</small>
      <small v-if="selectedPoi.address">{{ selectedPoi.address }}</small>
    </div>

    <div class="real-map-note">
      <span class="real-note-signal" aria-hidden="true"></span>
      <span>{{ stateMessage }}</span>
    </div>
    <div class="real-map-actions">
      <p>虚线内可选起点，外围可显示{{ analysisMode === 'cpp' ? '等时圈和街段' : '等时圈、采样路线和 POI 服务路线' }}<br /><strong>点击区内位置，记录候选起点</strong></p>
      <button type="button" :disabled="selectionDisabled || (mapState === 'ready' && boundaryState !== 'ready')" @click.stop="selectRegionCenter">选区域中心</button>
    </div>
    <div v-if="analysisResult?.coordinateSystem === 'preview-local-v1'" class="real-demo-legend" aria-label="合成示意图例">
      <span><i :class="cppSyntheticResult ? 'legend-area' : 'legend-circle'"></i>{{ cppSyntheticResult ? 'C++ 路网等时圈 · 近似面' : '固定半径示意' }}</span><span><i class="legend-route"></i>{{ cppSyntheticResult ? 'C++ 可达街段' : '临时路网路线' }}</span><span v-if="analysisResult?.accessLink"><i class="legend-access"></i>估算接入 · 未核实</span>
    </div>
    <div ref="probeEl" class="real-map-probe" :class="{ visible: probeVisible, outside: !hoverInside }" aria-hidden="true">
      <span class="real-probe-ring"></span><small class="real-probe-caption">{{ mapState === 'ready' && boundaryState !== 'ready' ? '选区未就绪' : hoverInside ? '选起点' : '区外' }}</small>
    </div>
    <p v-if="notice" class="real-map-toast" role="status">{{ notice }}</p>
  </div>
</template>
