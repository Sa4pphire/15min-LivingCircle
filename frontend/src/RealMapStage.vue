<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import BMapLoader from "@baidumap/jsapi-loader";
import boundaryWgs from "./data/demoBoundary.wgs84.json";
import preparedMapAsset from "./data/demoMap.bd09.json";
import { preparedMapCenter, preparedMapGeometry, sampleLocalArea, sampleLocalPath } from "./mapAsset.js";
import { baiduMapStyle } from "./baiduMapStyle";
import {
  baiduZoomForFactor, clampMapPan, clampMapZoomFactor,
  MAX_MAP_ZOOM_FACTOR, zoomFactor, zoomedFit,
} from "./mapZoom";
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
import { poiAccessLabel, poiCategoryStyles, visiblePois } from "./poiFacilities.js";
import { cppOverviewFit, cppOverviewInsets, cppOverviewPoints } from "./cppOverview.js";

const props = defineProps({
  candidate: { type: Object, default: null },
  analysisResult: { type: Object, default: null },
  zoomTier: { type: String, default: "medium" },
  overviewRequestId: { type: Number, default: 0 },
  poiFocusRequest: { type: Object, default: null },
  poiRoute: { type: Object, default: null },
  analysisMode: { type: String, default: "preview" },
  selectionDisabled: { type: Boolean, default: false },
  showBlindZones: { type: Boolean, default: false },
});
const emit = defineEmits(["select", "poi-select", "poi-dismiss"]);

const browserAk = import.meta.env.VITE_BAIDU_BROWSER_AK?.trim();
const boundaryWgsRing = boundaryWgs.geometry.coordinates[0];
const fallbackOrigin = [121.505, 31.333];
const fallbackRing = boundaryWgsRing.map((point) => wgsToLocal(point, fallbackOrigin));
const displayCornersLocal = expandedLocalBounds(fallbackRing);
const fallbackBoundary = geometryToSvgPath(boundaryWgs.geometry, (point) => wgsToLocal(point, fallbackOrigin));

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
// A container resize reveals more map; it is not a request to change scale.
const fallbackFitViewport = shallowRef(null);
const fallbackOverviewViewport = shallowRef(null);
const liveBoundaryPath = ref("");
const projectionVersion = ref(0);
const hoverInside = ref(true);
const probeVisible = ref(false);
const fallbackAnimated = ref(false);
const fallbackPan = ref({ x: 0, y: 0 });
const viewZoomFactor = ref(zoomFactor(props.zoomTier));
const manualZoom = ref(false);
// Freeze the camera's overview input until the user explicitly requests a
// refit. Clearing/replacing analysis layers must not move the map.
const overviewResult = shallowRef(props.zoomTier === "result" ? props.analysisResult : null);
const fallbackDragging = ref(false);
const selectedPoi = ref(null);
let map;
let BMap;
let observer;
let frame;
let probeFrame;
let wheelFrame;
let noticeTimer;
let fittedZoom;
let baseFittedZoom;
let baseFittedViewport;
let fittedCenter;
let fallbackDrag;
let suppressFallbackClick = false;
let lastNativeDragAt = 0;
let destroyed = false;
let mapSetupPromise;
let pendingPoiFocusRequest = null;

const fallbackActive = computed(() => mapState.value !== "ready");
const fallbackFit = computed(() => {
  const size = fallbackFitViewport.value ?? viewport.value;
  const fit = fitLocalPoints(displayCornersLocal, size.width, size.height, 0.04);
  return { ...fit,
    translateX: fit.translateX + (viewport.value.width - size.width) / 2,
    translateY: fit.translateY + (viewport.value.height - size.height) / 2 };
});
const fallbackView = computed(() => {
  const overviewSize = fallbackOverviewViewport.value ?? fallbackFitViewport.value ?? viewport.value;
  const overview = !manualZoom.value && props.zoomTier === "result" && props.analysisMode === "cpp"
    ? cppOverviewFit(overviewResult.value, overviewSize.width, overviewSize.height) : null;
  const fitted = zoomedFit(
    fallbackFit.value,
    viewport.value.width,
    viewport.value.height,
    props.zoomTier,
    props.candidate?.coordType === "wgs84ll"
      ? wgsToLocal([props.candidate.lng, props.candidate.lat], fallbackOrigin) : null,
    manualZoom.value ? viewZoomFactor.value : zoomFactor(props.zoomTier),
  );
  if (overview) {
    const scale = fallbackFit.value.scale * clampMapZoomFactor(overview.scale / fallbackFit.value.scale);
    const ratio = scale / overview.scale;
    fitted.scale = scale;
    fitted.translateX = viewport.value.width / 2 + (overview.translateX - overviewSize.width / 2) * ratio;
    fitted.translateY = viewport.value.height / 2 + (overview.translateY - overviewSize.height / 2) * ratio;
  }
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

// The route/area SVG is a sibling of the map container, not a child of a
// Baidu overlay pane. Its origin is the map viewport, so use pointToPixel.
// pointToOverlayPixel is only for overlays mounted inside BMap's pane and
// would introduce a pane offset here.
function mapPointToOverlayPixel(point) {
  if (!map || !BMap) return { x: 0, y: 0 };
  const projector = map.pointToPixel ?? map.pointToOverlayPixel;
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
  return `${0.08 + Math.max(0, Math.min(1, progress)) * 0.48}s`;
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
    routes: [],
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
    walkways: (props.analysisMode === "cpp" ? [] : result.reachableWalkways?.features ?? []).map((feature) =>
      projectGeometry(feature.geometry)),
    gaps: (result.blindZoneWalkways?.features ?? []).map((feature) =>
      projectGeometry(feature.geometry)),
    blindZones: (props.showBlindZones ? result.blindZones?.features ?? [] : []).map((feature) =>
      projectGeometry(feature.geometry)),
  };
});
const poiMarkers = computed(() => {
  projectionVersion.value;
  return visiblePois(props.analysisResult).flatMap((poi) => {
    if (mapState.value === "ready" && map && BMap) {
      const coordinate = finitePoint(poi.bd09) ? poi.bd09
        : finitePoint(poi.point) ? localToBd09(poi.point) : null;
      if (!finitePoint(coordinate)) return [];
      const pixel = mapPointToOverlayPixel(coordinate);
      return [{ ...poi, pixel: [pixel.x, pixel.y] }];
    }
    const point = poiLocalPoint(poi);
    if (!point) return [];
    const { scale, translateX, translateY } = fallbackView.value;
    return [{ ...poi, pixel: [translateX + point[0] * scale, translateY + point[1] * scale] }];
  }).sort((a, b) => Number(a.id === selectedPoi.value?.id) - Number(b.id === selectedPoi.value?.id));
});
const selectedPoiRoute = computed(() => props.analysisMode === "cpp" &&
  props.poiRoute?.poiId === selectedPoi.value?.id ? props.poiRoute : null);
const selectedRoutePaths = computed(() => {
  projectionVersion.value;
  const route = selectedPoiRoute.value;
  if (route?.status !== "ready") return [];
  return route.segments.map(segment => {
    const points = mapState.value === "ready" && map && BMap
      ? sampleLocalPath(segment.points).map(point => {
        const pixel = mapPointToOverlayPixel(localToBd09(point));
        return [pixel.x, pixel.y];
      }) : segment.points.map(point => {
        const { scale, translateX, translateY } = fallbackView.value;
        return [translateX + point[0] * scale, translateY + point[1] * scale];
      });
    return { ...segment, d: linePath(points), delay: `${segment.startProgress * .65}s`,
      access: ["origin_access", "facility_access"].includes(segment.kind) };
  });
});

function selectPoi(poi) {
  selectedPoi.value = poi;
  emit("poi-select", poi);
}

function dismissPoi() {
  selectedPoi.value = null;
  emit("poi-dismiss");
}

function finitePoint(point) {
  return Array.isArray(point) && point.length === 2 && point.every(Number.isFinite);
}

function poiLocalPoint(poi) {
  return finitePoint(poi.point) ? poi.point : finitePoint(poi.bd09) ? bd09ToLocal(poi.bd09) : null;
}

function focusRequestedPoi() {
  const request = pendingPoiFocusRequest;
  if (!request || props.analysisMode !== "cpp") return;
  const poi = visiblePois(props.analysisResult).find(entry => entry.id === request.id);
  if (!poi) {
    pendingPoiFocusRequest = null;
    selectedPoi.value = null;
    return;
  }
  // The map stays mounted while the report is shown. Wait for real dimensions
  // (or SDK initialization) without starting another analysis or changing zoom.
  if (viewport.value.width <= 1 || viewport.value.height <= 1) return;
  if (mapState.value === "ready" && map && BMap) {
    const coordinate = finitePoint(poi.bd09) ? poi.bd09
      : finitePoint(poi.point) ? localToBd09(poi.point) : null;
    if (!finitePoint(coordinate)) {
      pendingPoiFocusRequest = null;
      selectedPoi.value = null;
      showNotice("该设施缺少可用的地图坐标，暂时无法定位。");
      return;
    }
    const level = map.getZoom();
    if (!Number.isFinite(level)) return;
    map.centerAndZoom(new BMap.Point(...coordinate), level,
      { noAnimation: true, callback: scheduleProjection });
    scheduleProjection();
    pendingPoiFocusRequest = null;
  } else {
    // BD-09-only cached POIs can also be displayed on the SVG fallback using
    // the prepared calibration. Never treat their coordinates as WGS-84.
    if (!mapAlignment.value && finitePoint(poi.bd09)) loadPreparedBoundary();
    const point = poiLocalPoint(poi);
    if (!point) {
      pendingPoiFocusRequest = null;
      selectedPoi.value = null;
      showNotice("该设施缺少可用的地图坐标，暂时无法定位。");
      return;
    }
    const { scale, translateX, translateY } = fallbackView.value;
    fallbackPan.value = {
      x: fallbackPan.value.x + viewport.value.width / 2 - (translateX + point[0] * scale),
      y: fallbackPan.value.y + viewport.value.height / 2 - (translateY + point[1] * scale),
    };
    // Retain a pending request only while the native SDK is still loading.
    if (mapState.value !== "loading") pendingPoiFocusRequest = null;
  }
  selectedPoi.value = poi;
  nextTick(() => {
    if (!destroyed && selectedPoi.value?.id === poi.id) {
      stageEl.value?.querySelector(".real-poi-marker.is-selected")?.focus({ preventScroll: true });
    }
  });
}

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
  const overview = !manualZoom.value && props.zoomTier === "result" && props.analysisMode === "cpp"
    ? cppOverviewPoints(overviewResult.value) : [];
  // A requested result overview needs one fit, not another fit of the same
  // base region first. Its established scale baseline remains unchanged.
  const base = overview.length && baseFittedViewport ? baseFittedViewport
    : map.getViewport(displayCornersBd09.value.map(([lng, lat]) => new BMap.Point(lng, lat)), {
    margins: [margin, margin, margin, margin],
  });
  if (!Number.isFinite(base?.zoom) || !base.center) return;
  baseFittedViewport = base;
  baseFittedZoom = base.zoom;
  const points = overview.length ? [
    ...overview.map(localToBd09), ...visiblePois(overviewResult.value).map(poi => poi.bd09),
  ].filter(point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite))
    : displayCornersBd09.value;
  const inset = cppOverviewInsets(viewport.value.width, viewport.value.height);
  // Calculate only: setViewport schedules a camera change, so immediately
  // reading getZoom/getCenter after it captures the previous (already scaled)
  // view. Reusing that value makes each tier switch multiply the zoom again.
  const fitted = overview.length ? map.getViewport(points.map(([lng, lat]) => new BMap.Point(lng, lat)), {
    margins: [inset.top, inset.right, inset.bottom, inset.left],
  }) : base;
  if (!Number.isFinite(fitted?.zoom) || !fitted.center) return;
  fittedZoom = fitted.zoom;
  fittedCenter = fitted.center;
  // One absolute camera target, with no preceding queued viewport mutation.
  applyBaiduZoom(animate);
}

function applyBaiduZoom(animate = true) {
  if (!map || !BMap || !Number.isFinite(fittedZoom) || !fittedCenter) return;
  // Scale presets must not disable the primary left-button map gesture.
  map.enableDragging?.();
  const candidate = props.candidate?.coordType === "bd09ll" ? props.candidate : null;
  const center = manualZoom.value ? (map.getCenter() ?? fittedCenter)
    : props.zoomTier === "large" && candidate
      ? new BMap.Point(candidate.lng, candidate.lat) : fittedCenter;
  const zoom = baiduZoomForFactor(
    manualZoom.value ? baseFittedZoom : fittedZoom,
    manualZoom.value ? viewZoomFactor.value : zoomFactor(props.zoomTier),
    Math.max(baseFittedZoom, map.getMinZoom?.() ?? 3),
    Math.min(baseFittedZoom + Math.log2(MAX_MAP_ZOOM_FACTOR), map.getMaxZoom?.() ?? 21),
  );
  viewZoomFactor.value = clampMapZoomFactor(2 ** (zoom - baseFittedZoom));
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

function handleMapWheel(event) {
  if (event.ctrlKey || !Number.isFinite(event.deltaY) || event.deltaY === 0 ||
    Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  // Consume vertical wheel gestures even at either limit, so they never turn pages.
  event.preventDefault();
  const delta = event.deltaY * (event.deltaMode === 1 ? 16
    : event.deltaMode === 2 ? viewport.value.height : 1);
  const previous = fallbackActive.value && !manualZoom.value && props.zoomTier === "result"
    ? clampMapZoomFactor(fallbackView.value.scale / fallbackFit.value.scale) : viewZoomFactor.value;
  const next = clampMapZoomFactor(previous * 2 ** (-Math.max(-240, Math.min(240, delta)) / 400));
  if (Math.abs(next - previous) < 1e-6) return;

  // Preserve the fallback camera center when leaving an explicitly fitted overview.
  const before = fallbackView.value;
  const center = [(viewport.value.width / 2 - before.translateX) / before.scale,
    (viewport.value.height / 2 - before.translateY) / before.scale];
  viewZoomFactor.value = next;
  manualZoom.value = true;
  if (fallbackActive.value) {
    const after = fallbackView.value;
    fallbackPan.value = {
      x: fallbackPan.value.x + viewport.value.width / 2 - (after.translateX + center[0] * after.scale),
      y: fallbackPan.value.y + viewport.value.height / 2 - (after.translateY + center[1] * after.scale),
    };
  }
  // Only one camera write per frame, including rapid mouse/trackpad wheel input.
  if (!wheelFrame) wheelFrame = requestAnimationFrame(() => {
    wheelFrame = 0;
    if (!destroyed && mapState.value === "ready") applyBaiduZoom(false);
  });
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
    event.target.closest("button, a, .real-map-toast, .real-poi-marker, .real-poi-popover")) return;
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
  if (!fallbackActive.value ||
    (event.pointerType === "mouse" && event.button !== 0) ||
    event.target.closest("button, a, .real-map-toast, .real-poi-marker, .real-poi-popover")) return;
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

function moveProbe(event) {
  if (!stageEl.value || !probeEl.value || props.selectionDisabled ||
    (event.pointerType && event.pointerType !== "mouse") || event.buttons > 0 ||
    !window.matchMedia("(hover: hover) and (pointer: fine)").matches ||
    event.target?.closest("button, a, .real-map-toast, .real-poi-marker, .real-poi-popover")) {
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
    const point = (map.pixelToPoint ?? map.overlayPixelToPoint).call(
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
    baseFittedZoom = fittedZoom;
    fittedCenter = map.getCenter();
    map.enableDragging?.();
    // A single bounded controller handles both the native map and SVG fallback.
    map.disableScrollWheelZoom?.();
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
  // ResizeObserver runs throughout the footer animation. Preserve the user's
  // zoom and dragged centre; only redraw the viewport-aligned SVG layers.
  if (!stageEl.value?.clientWidth || !stageEl.value.clientHeight) return;
  if (!fallbackFitViewport.value) fallbackFitViewport.value = {
    width: stageEl.value.clientWidth, height: stageEl.value.clientHeight,
  };
  if (stageEl.value.clientWidth === viewport.value.width &&
    stageEl.value.clientHeight === viewport.value.height) return;
  viewport.value = {
    width: Math.max(1, stageEl.value.clientWidth),
    height: Math.max(1, stageEl.value.clientHeight),
  };
  if (mapState.value === "ready") scheduleProjection();
}

function handleNativeDragEnd() {
  lastNativeDragAt = performance.now();
  scheduleProjection();
}

watch([() => props.zoomTier, () => props.overviewRequestId], () => {
  if (wheelFrame) cancelAnimationFrame(wheelFrame);
  wheelFrame = 0;
  manualZoom.value = false;
  viewZoomFactor.value = zoomFactor(props.zoomTier);
  overviewResult.value = props.zoomTier === "result" ? props.analysisResult : null;
  fallbackOverviewViewport.value = { ...viewport.value };
  fallbackPan.value = { x: 0, y: 0 };
  if (mapState.value === "ready") fitBaiduViewport(true);
});
watch(() => props.candidate, () => {
  pendingPoiFocusRequest = null;
  selectedPoi.value = null;
  if (props.zoomTier === "large" && !manualZoom.value) {
    fallbackPan.value = { x: 0, y: 0 };
    if (mapState.value === "ready") applyBaiduZoom();
  } else if (mapState.value === "ready") scheduleProjection();
});
watch(() => props.analysisResult, (next, previous) => {
  if (!next?.analysisId || next.analysisId !== previous?.analysisId) {
    pendingPoiFocusRequest = null;
    selectedPoi.value = null;
  }
  if (mapState.value === "ready") scheduleProjection();
});
watch(() => props.analysisMode, () => {
  pendingPoiFocusRequest = null;
  selectedPoi.value = null;
  notice.value = "";
  leaveProbe();
  overviewResult.value = props.zoomTier === "result" ? props.analysisResult : null;
  if (mapState.value === "ready") scheduleProjection();
});
watch(() => props.selectionDisabled, (disabled) => {
  if (disabled) leaveProbe();
});
watch(() => props.poiFocusRequest, (request) => {
  pendingPoiFocusRequest = request;
  focusRequestedPoi();
}, { flush: "post", immediate: true });
watch([mapState, viewport], () => {
  if (pendingPoiFocusRequest) focusRequestedPoi();
}, { flush: "post" });
onMounted(() => {
  updateSize();
  observer = new ResizeObserver(updateSize);
  observer.observe(stageEl.value);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!destroyed) fallbackAnimated.value = true;
  }));
  if (browserAk) setupBaidu();
  else {
    loadPreparedBoundary();
    loadFallbackContext();
  }
});
onUnmounted(() => {
  destroyed = true;
  observer?.disconnect();
  clearTimeout(noticeTimer);
  if (frame) cancelAnimationFrame(frame);
  if (probeFrame) cancelAnimationFrame(probeFrame);
  if (wheelFrame) cancelAnimationFrame(wheelFrame);
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
    :class="{ 'is-outside': !hoverInside, 'can-pan': fallbackActive, 'is-panning': fallbackDragging, 'can-animate': fallbackAnimated, 'has-cpp-result': cppSyntheticResult }"
    role="group"
    aria-label="四路围合演示区域地图，可点击区域内位置设置候选起点"
    @click="handleFallbackClick"
    @pointerdown="beginFallbackDrag"
    @pointermove="handlePointerMove"
    @pointerup="endFallbackDrag"
    @pointercancel="endFallbackDrag"
    @lostpointercapture="endFallbackDrag"
    @pointerleave="leaveProbe"
    @wheel.capture.stop="handleMapWheel"
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
          <path v-for="segment in analysisMode === 'cpp' ? [] : fallbackDemoResult.routeSegments" :key="segment.id" :d="linePath(segment.points)" class="real-demo-route" pathLength="1" :style="{ '--route-delay': routeDelay(segment, fallbackDemoResult) }" />
          <path v-if="analysisMode !== 'cpp' && fallbackDemoResult.accessLink?.length > 2" :d="linePath(fallbackDemoResult.accessLink.points)" class="real-demo-access" />
        </g>
      </g>
      <g v-if="fallbackCandidate" :style="{ transform: fallbackCandidateTransform }" class="real-candidate-mark" aria-hidden="true">
        <g class="real-candidate-glyph">
          <circle r="6" class="real-candidate-halo" /><circle r="3.5" class="real-candidate-dot" />
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
        <path v-if="analysisMode !== 'cpp' && analysisResult.accessLink?.length > 2" :d="liveDemoResult.access" class="real-demo-access" />
      </g>
      <g v-if="liveCandidate" :transform="`translate(${liveCandidate.x} ${liveCandidate.y})`" class="real-candidate-mark">
        <g class="real-candidate-glyph">
          <circle r="6" class="real-candidate-halo" /><circle r="3.5" class="real-candidate-dot" />
        </g>
      </g>
    </svg>

    <svg v-if="selectedRoutePaths.length" class="real-cpp-route-layer"
      :viewBox="`0 0 ${viewport.width} ${viewport.height}`" aria-hidden="true">
      <g :key="`${selectedPoiRoute.poiId}:${selectedPoiRoute.sequence}`">
        <path v-for="segment in selectedRoutePaths" :key="`casing-${segment.id}`" :d="segment.d" class="real-cpp-route-casing" />
        <path v-for="segment in selectedRoutePaths" :key="segment.id" :d="segment.d"
          class="real-cpp-selected-route" :class="{ 'is-access': segment.access }"
          :pathLength="segment.access ? undefined : 1" :style="{ '--route-delay': segment.delay }" />
      </g>
    </svg>
    <svg v-if="analysisMode === 'cpp' && analysisResult" class="real-poi-layer"
      :viewBox="`0 0 ${viewport.width} ${viewport.height}`" role="group" aria-label="等时圈内基础设施候选点位">
      <g v-for="poi in poiMarkers" :key="poi.id" class="real-poi-marker"
        :class="{ 'is-selected': selectedPoi?.id === poi.id }" :style="{ '--poi-color': poiCategoryStyles[poi.category].color }"
        :transform="`translate(${poi.pixel[0]} ${poi.pixel[1]})`" tabindex="0" role="button"
        :aria-pressed="selectedPoi?.id === poi.id"
        :aria-label="`${poiCategoryStyles[poi.category].label}：${poi.name}`"
        @pointerdown.stop @click.stop="selectPoi(poi)" @keydown.enter.stop="selectPoi(poi)" @keydown.space.prevent.stop="selectPoi(poi)">
        <title>{{ poi.name }} · {{ poiAccessLabel(poi) }}</title>
        <g v-if="selectedPoi?.id === poi.id" class="real-poi-selection" aria-hidden="true">
          <circle r="19" class="real-poi-selection-ring" />
          <circle :key="poiFocusRequest?.sequence ?? poi.id" r="25" class="real-poi-selection-pulse" />
        </g>
        <circle r="11" :stroke="poiCategoryStyles[poi.category].color" />
        <text text-anchor="middle" dominant-baseline="central" :fill="poiCategoryStyles[poi.category].color">{{ poiCategoryStyles[poi.category].glyph }}</text>
      </g>
    </svg>
    <div v-if="selectedPoi" class="real-poi-popover" role="status" @pointerdown.stop @click.stop>
      <button type="button" aria-label="关闭设施详情" @click.stop="dismissPoi">×</button>
      <strong>{{ selectedPoi.name }}</strong>
      <span>{{ poiCategoryStyles[selectedPoi.category].label }} · 百度 POI</span>
      <small v-if="selectedPoiRoute?.destinationAccessMode !== 'estimated_straight_line'">{{ poiAccessLabel(selectedPoi) }}</small>
      <small v-if="selectedPoiRoute?.status === 'loading'">正在计算 Dijkstra 最短路径…</small>
      <template v-else-if="selectedPoiRoute?.status === 'ready'">
        <strong class="real-cpp-route-summary">{{ (selectedPoiRoute.travelTimeSeconds / 60).toFixed(1) }} 分钟 · {{ Math.round(selectedPoiRoute.lengthMeters) }} 米</strong>
        <small>过街等待 {{ Math.round(selectedPoiRoute.crossingWaitSeconds) }} 秒 · {{ selectedPoiRoute.destinationAccessMode === 'estimated_straight_line' ? '路线终点为 POI 点位' : '路线终点为绑定入口' }}</small>
        <small v-if="selectedPoiRoute.destinationAccessMode === 'estimated_straight_line'" class="real-cpp-route-warning">直线穿越地块约 {{ Math.round(selectedPoiRoute.destinationAccessDistanceMeters) }} 米 · 未核实，未考虑建筑／围墙</small>
        <small v-if="!selectedPoiRoute.withinThreshold">该路径超过 15 分钟，展示面内的点不一定路网可达。</small>
      </template>
      <small v-else-if="selectedPoiRoute?.message">{{ selectedPoiRoute.message }}</small>
      <button v-if="selectedPoiRoute?.status === 'error'" type="button" class="real-cpp-route-retry" @click.stop="selectPoi(selectedPoi)">重试路线</button>
      <small v-if="selectedPoi.address">{{ selectedPoi.address }}</small>
    </div>

    <div v-if="analysisResult?.coordinateSystem === 'preview-local-v1'" class="real-demo-legend" aria-label="合成示意图例">
      <span><i :class="cppSyntheticResult ? 'legend-area' : 'legend-circle'"></i>{{ cppSyntheticResult ? 'C++ 路网等时圈 · 近似面' : '固定半径示意' }}</span>
      <span v-if="analysisMode === 'cpp'"><i class="legend-cpp-route"></i>{{ selectedPoiRoute?.status === 'ready' ? '选中设施的 Dijkstra 路径' : '点击设施查看最短路径' }}</span>
      <span v-else><i class="legend-route"></i>临时路网路线</span>
      <span v-if="selectedPoiRoute?.status === 'ready' && selectedRoutePaths.some(segment => segment.access)"><i class="legend-access"></i>估算接入 · 未核实</span>
    </div>
    <div ref="probeEl" class="real-map-probe" :class="{ visible: probeVisible, outside: !hoverInside }" aria-hidden="true">
      <span class="real-probe-ring"></span><small class="real-probe-caption">{{ mapState === 'ready' && boundaryState !== 'ready' ? '选区未就绪' : hoverInside ? '选起点' : '区外' }}</small>
    </div>
    <p v-if="notice" class="real-map-toast" role="status">{{ notice }}</p>
  </div>
</template>
