<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, useId, watch } from "vue";
import BMapLoader from "@baidumap/jsapi-loader";
import { createLocalBd09Alignment, sampleLocalArea, sampleLocalPath } from "./mapAsset.js";
import { getActiveRegion } from "./regionLoader.js";
import { boundsCorners, containsBounds, constrainMapView, metricScale, coverageCorrection } from "./mapCoverage.js";
import { baiduMapStyle } from "./baiduMapStyle";
import {
  baiduZoomForFactor, clampMapZoomFactor,
  MAX_MAP_ZOOM_FACTOR, zoomFactor, zoomedFit,
} from "./mapZoom";
import {
  fitLocalPoints,
  geometryToSvgPath,
  localToWgs,
  wgsToLocal,
} from "./mapGeometry";
import "./realMap.css";
import { poiAccessLabel, poiCategoryStyles, visiblePois } from "./poiFacilities.js";
import { cppOverviewFit, cppOverviewInsets, cppOverviewPoints } from "./cppOverview.js";

const props = defineProps({
  candidate: { type: Object, default: null },
  analysisResult: { type: Object, default: null },
  analysisComplete: { type: Boolean, default: false },
  zoomTier: { type: String, default: "medium" },
  overviewRequestId: { type: Number, default: 0 },
  poiFocusRequest: { type: Object, default: null },
  poiRoute: { type: Object, default: null },
  analysisMode: { type: String, default: "preview" },
  selectionDisabled: { type: Boolean, default: false },
  showBlindZones: { type: Boolean, default: false },
});
const emit = defineEmits(["select", "poi-select", "poi-dismiss"]);
const blindClipId = useId();

const browserAk = import.meta.env.VITE_BAIDU_BROWSER_AK?.trim();
const region = getActiveRegion();
const preparedMapAsset = region.alignment;
const fallbackOrigin = region.geographicOrigin;
const coverageBounds = region.displayBounds;
const displayCornersLocal = boundsCorners(coverageBounds);
const fallbackRing = [...displayCornersLocal, displayCornersLocal[0]];
const fallbackBoundary = geometryToSvgPath({ type: "Polygon", coordinates: [fallbackRing] }, point => point);

const stageEl = ref(null);
const baiduEl = ref(null);
const contextEl = ref(null);
const probeEl = ref(null);
const mapState = ref(browserAk ? "loading" : "no-key");
const mapError = ref("");
const boundaryState = ref("pending");
const boundaryError = ref("");
const notice = ref("");
const context = shallowRef(region.context);
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
const fallbackCamera = computed(() => {
  const overviewSize = fallbackOverviewViewport.value ?? fallbackFitViewport.value ?? viewport.value;
  const overview = !manualZoom.value && props.zoomTier === "result" && props.analysisMode === "cpp"
    ? cppOverviewFit(overviewResult.value, overviewSize.width, overviewSize.height) : null;
  const fitted = zoomedFit(
    fallbackFit.value,
    viewport.value.width,
    viewport.value.height,
    props.zoomTier,
    props.candidate?.coordType === region.geographicCoordType
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
  return fitted;
});
const fallbackView = computed(() => constrainMapView({ ...fallbackCamera.value,
  translateX: fallbackCamera.value.translateX + fallbackPan.value.x,
  translateY: fallbackCamera.value.translateY + fallbackPan.value.y,
}, viewport.value.width, viewport.value.height, coverageBounds));

function setFallbackPan(pan) {
  const camera = fallbackCamera.value;
  const limited = constrainMapView({ ...camera, translateX: camera.translateX + pan.x,
    translateY: camera.translateY + pan.y }, viewport.value.width, viewport.value.height, coverageBounds);
  const x = (viewport.value.width / 2 - limited.translateX) / limited.scale;
  const y = (viewport.value.height / 2 - limited.translateY) / limited.scale;
  fallbackPan.value = { x: viewport.value.width / 2 - x * camera.scale - camera.translateX,
    y: viewport.value.height / 2 - y * camera.scale - camera.translateY };
}

function setFallbackCenter([x, y]) {
  const camera = fallbackCamera.value;
  setFallbackPan({ x: viewport.value.width / 2 - x * camera.scale - camera.translateX,
    y: viewport.value.height / 2 - y * camera.scale - camera.translateY });
}

const scaleBar = computed(() => {
  projectionVersion.value;
  if (mapState.value === "ready" && map && BMap) {
    const projector = map.pixelToPoint ?? map.overlayPixelToPoint;
    if (projector) {
      const at = offset => {
        const point = projector.call(map, new BMap.Pixel(viewport.value.width / 2 + offset,
          viewport.value.height / 2));
        return bd09ToLocal([point.lng, point.lat]);
      };
      const a = at(-40), b = at(40);
      if (a && b) return metricScale(Math.hypot(b[0] - a[0], b[1] - a[1]) / 80);
    }
    return null;
  }
  return metricScale(1 / fallbackView.value.scale);
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
  if (props.candidate?.coordType !== region.geographicCoordType) return null;
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

const fallbackDemoResult = computed(() => {
  const result = props.analysisResult;
  if (result?.coordinateSystem === 'preview-local-v1') return result;
  if (result?.source === 'baidu-sampled-idw' && result.fallbackDisplayArea) {
    return { ...result, displayArea: result.fallbackDisplayArea, routeSegments: [], accessLink: null };
  }
  return null;
});
function blindZoneStyle(missingCategories = []) {
  const known = missingCategories.filter(category => Object.hasOwn(poiCategoryStyles, category));
  const color = known.length === 1 ? poiCategoryStyles[known[0]].color
    : known.length === Object.keys(poiCategoryStyles).length ? '#d3484a' : '#855ba7';
  return { '--blind-fill': `${color}38`, '--blind-stroke': `${color}99` };
}
const fallbackBlindZones = computed(() => {
  const result = props.analysisResult;
  if (!props.showBlindZones || result?.source !== 'baidu-sampled-idw' || !mapAlignment.value) return [];
  return (result.blindZones?.features ?? []).flatMap(feature => {
    try {
      const d = geometryToSvgPath(feature.geometry, point => {
        const local = bd09ToLocal(point);
        if (!finitePoint(local)) throw new Error('OUTSIDE_ALIGNMENT');
        return local;
      });
      return d ? [{ d, missingCategories: feature.properties?.missingCategories ?? [] }] : [];
    } catch { return []; }
  });
});
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
    poiRoutes: (props.analysisMode === "preview" && !props.poiRoute ? result.routeSegments ?? [] : []).map((segment) => ({
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
    blindZones: (props.showBlindZones ? result.blindZones?.features ?? [] : []).map((feature) => ({
      d: projectGeometry(feature.geometry), missingCategories: feature.properties?.missingCategories ?? [],
    })),
  };
});
const poiMarkers = computed(() => {
  projectionVersion.value;
  if (!props.candidate || !props.analysisComplete || !props.analysisResult) return [];
  const candidates = visiblePois(props.analysisResult);
  return candidates.flatMap((poi) => {
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
const selectedPoiRoute = computed(() => props.poiRoute?.poiId === selectedPoi.value?.id ? props.poiRoute : null);
const selectedRoutePaths = computed(() => {
  projectionVersion.value;
  const route = selectedPoiRoute.value;
  if (route?.status !== "ready") return [];
  const geographic = route.coordinateSystem === 'bd09ll';
  return route.segments.flatMap(segment => {
    const samples = geographic ? segment.points : mapState.value === 'ready' ? sampleLocalPath(segment.points) : segment.points;
    const parts = [];
    let part = [];
    for (const point of samples) {
      let projected = null;
      if (mapState.value === 'ready' && map && BMap) {
        const coordinate = geographic ? point : localToBd09(point);
        if (finitePoint(coordinate)) {
          const pixel = mapPointToOverlayPixel(coordinate);
          projected = [pixel.x, pixel.y];
        }
      } else {
        const local = geographic ? bd09ToLocal(point) : point;
        if (finitePoint(local)) {
          const { scale, translateX, translateY } = fallbackView.value;
          projected = [translateX + local[0] * scale, translateY + local[1] * scale];
        }
      }
      if (finitePoint(projected)) part.push(projected);
      else { if (part.length >= 2) parts.push(part); part = []; }
    }
    if (part.length >= 2) parts.push(part);
    return parts.map((points, index) => ({ ...segment, id: `${segment.id}:${index}`, d: linePath(points),
      delay: `${segment.startProgress * .65}s`, baidu: geographic,
      access: ["origin_access", "facility_access"].includes(segment.kind) }));
  });
});
const selectedRouteClipped = computed(() => selectedPoiRoute.value?.status === 'ready' &&
  selectedPoiRoute.value.coordinateSystem === 'bd09ll' && mapState.value !== 'ready' &&
  selectedPoiRoute.value.segments.some(segment => segment.points.some(point => !finitePoint(bd09ToLocal(point)))));

function selectPoi(poi) {
  selectedPoi.value = poi;
  if (props.analysisResult) emit("poi-select", poi);
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
  if (!request) return;
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
    setFallbackCenter(point);
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
  if (!destroyed) context.value = region.context;
}

function scheduleProjection() {
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = 0;
    if (!map || !BMap) return;
    constrainNativeViewport();
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

let constrainingNative = false;
function constrainNativeViewport() {
  if (constrainingNative || !map || !BMap || !boundaryBd09.value?.length) return;
  const pixels = boundaryBd09.value.map(mapPointToOverlayPixel);
  const xs = pixels.map(p => p.x), ys = pixels.map(p => p.y);
  const west = Math.min(...xs), east = Math.max(...xs);
  const north = Math.min(...ys), south = Math.max(...ys);
  const width = viewport.value.width, height = viewport.value.height;
  const ratio = Math.max(width / (east - west), height / (south - north));
  if (![ratio, west, east, north, south].every(Number.isFinite) || ratio <= 0) return;
  let level = map.getZoom();
  let target = map.getCenter();
  if (ratio > 1.001) {
    level = Math.ceil(level + Math.log2(ratio));
    map.setMinZoom?.(level);
  } else {
    const dx = west > 0 ? -west : east < width ? width - east : 0;
    const dy = north > 0 ? -north : south < height ? height - south : 0;
    const unproject = map.pixelToPoint ?? map.overlayPixelToPoint;
    if (!unproject) return;
    if (Math.abs(dx) >= .5 || Math.abs(dy) >= .5) {
      target = unproject.call(map, new BMap.Pixel(width / 2 - dx, height / 2 - dy));
    } else {
      // The official datum grid is nonlinear. Check the viewport in local
      // meters too, rather than trusting only its projected bounding box.
      const samples = [[0,0], [width/2,0], [width,0], [width,height/2],
        [width,height], [width/2,height], [0,height], [0,height/2]].map(([x,y]) => {
        const point = unproject.call(map, new BMap.Pixel(x,y));
        return bd09ToLocal([point.lng, point.lat]);
      });
      if (samples.some(point => !point)) return;
      const correction = coverageCorrection(samples, coverageBounds);
      if (correction.zoomRatio > 1.0001) {
        level = Math.ceil(level + Math.log2(correction.zoomRatio));
        map.setMinZoom?.(level);
      } else if (Math.abs(correction.dx) > .05 || Math.abs(correction.dy) > .05) {
        const center = bd09ToLocal([target.lng, target.lat]);
        if (!center) return;
        const coordinate = localToBd09([center[0] + correction.dx, center[1] + correction.dy]);
        if (!coordinate) return;
        target = new BMap.Point(...coordinate);
      } else return;
    }
  }
  constrainingNative = true;
  try { map.centerAndZoom(target, level, { noAnimation: true }); }
  finally { constrainingNative = false; }
  viewZoomFactor.value = clampMapZoomFactor(2 ** (level - baseFittedZoom));
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
  const previous = fallbackActive.value
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
    setFallbackCenter(center);
  }
  // Only one camera write per frame, including rapid mouse/trackpad wheel input.
  if (!wheelFrame) wheelFrame = requestAnimationFrame(() => {
    wheelFrame = 0;
    if (!destroyed && mapState.value === "ready") applyBaiduZoom(false);
  });
}

function zoomMap(direction) {
  handleMapWheel({ deltaY: -direction * 200, deltaX: 0, deltaMode: 0, ctrlKey: false,
    preventDefault() {} });
}

function resetMapViewport() {
  manualZoom.value = true;
  viewZoomFactor.value = 1;
  fallbackPan.value = { x: 0, y: 0 };
  if (mapState.value === "ready" && map && baseFittedViewport) {
    map.centerAndZoom(baseFittedViewport.center, baseFittedZoom, { noAnimation: true });
    scheduleProjection();
  }
}

function loadPreparedBoundary() {
  try {
    const alignment = createLocalBd09Alignment(preparedMapAsset);
    const corners = displayCornersLocal.map(alignment.toBd09);
    const ring = sampleLocalPath(fallbackRing).map(alignment.toBd09);
    if (corners.some(point => !point) || ring.some(point => !point)) {
      throw new Error("路网范围超出本地坐标校准网格");
    }
    boundaryBd09.value = ring;
    displayCornersBd09.value = corners;
    mapAlignment.value = alignment;
    regionCenterBd09.value = alignment.toBd09([
      (coverageBounds.minX + coverageBounds.maxX) / 2,
      (coverageBounds.minY + coverageBounds.maxY) / 2,
    ]);
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
  if (coordType === "bd09ll" && !localPoint && boundaryState.value !== "ready") {
    showNotice("选区数据尚未准备好，暂不能在百度底图上选点。");
    return;
  }
  const local = localPoint ?? (coordType === "bd09ll"
    ? bd09ToLocal([lng, lat]) : wgsToLocal([lng, lat], fallbackOrigin));
  if (!local) {
    showNotice("坐标尚未准备好，请稍后再选点。");
    return;
  }
  if (!containsBounds(local, coverageBounds)) {
    showNotice("当前位置超出现有路网数据范围，请在有数据的地区选点。");
    return;
  }
  emit("select", { lng, lat, coordType, local: { x: local[0], y: local[1] } });
  showNotice(props.analysisMode === "cpp"
    ? "起点已选；点击右上角，计算模拟步行范围。"
    : "起点已选；点击右上角，在线计算步行范围。");
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
  selectPoint(lng, lat, region.geographicCoordType, local);
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
  setFallbackPan({ x: fallbackDrag.pan.x + dx, y: fallbackDrag.pan.y + dy });
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
    const local = bd09ToLocal([point.lng, point.lat]);
    hoverInside.value = Boolean(local && containsBounds(local, coverageBounds));
  } else {
    const local = fallbackPointFromClient(event.clientX, event.clientY);
    if (local) hoverInside.value = containsBounds(local, coverageBounds);
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
    console.warn("百度底图初始化失败，已切换到区域包的本地地图预览。");
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
watch(() => props.analysisComplete, (complete) => {
  if (!complete) {
    pendingPoiFocusRequest = null;
    selectedPoi.value = null;
  }
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
    aria-label="现有路网数据范围地图，可点击范围内位置设置候选起点"
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

    <div class="real-map-navigation" @pointerdown.stop @click.stop @dblclick.stop>
      <div class="real-map-zoom-buttons" role="group" aria-label="地图比例尺控制">
        <button type="button" aria-label="放大地图" title="放大" @click="zoomMap(1)">＋</button>
        <button type="button" aria-label="缩小地图" title="缩小" @click="zoomMap(-1)">−</button>
        <button type="button" class="real-map-reset" aria-label="回到路网范围中心" @click="resetMapViewport">复位</button>
      </div>
      <div v-if="scaleBar" class="real-map-scale" :aria-label="`地图比例尺 ${scaleBar.label}`">
        <span>{{ scaleBar.label }}</span><i :style="{ width: `${scaleBar.pixels}px` }"></i>
      </div>
    </div>

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
        <clipPath :id="`${blindClipId}-fallback`" clipPathUnits="userSpaceOnUse">
          <path v-if="fallbackDemoResult?.displayArea.type === 'polygon'"
            :d="localAreaPath(fallbackDemoResult)" clip-rule="evenodd" fill-rule="evenodd" />
        </clipPath>
      </defs>
      <rect :width="viewport.width" :height="viewport.height" fill="#eaf0ec" />
      <rect :width="viewport.width" :height="viewport.height" fill="url(#real-map-grid)" opacity=".45" />
      <g ref="contextEl" :style="{ transform: fallbackTransform }" class="real-context">
        <path v-for="feature in contextGroups.waterArea" :key="feature.id" :d="feature.d" :fill-rule="feature.fillRule || 'nonzero'" class="real-water-area" />
        <path v-for="feature in contextGroups.park" :key="feature.id" :d="feature.d" :fill-rule="feature.fillRule || 'nonzero'" class="real-park-area" />
        <path v-for="feature in contextGroups.building" :key="feature.id" :d="feature.d" :fill-rule="feature.fillRule || 'nonzero'" class="real-building" />
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
        <g v-if="fallbackDemoResult?.displayArea.type === 'polygon'" class="real-blind-zones"
          :clip-path="`url(#${blindClipId}-fallback)`">
          <path v-for="(zone, index) in fallbackBlindZones" :key="`blind-${index}`" :d="zone.d"
            class="real-blind-zone" :style="blindZoneStyle(zone.missingCategories)" fill-rule="evenodd" />
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
      <defs>
        <clipPath :id="`${blindClipId}-live`" clipPathUnits="userSpaceOnUse">
          <path :d="liveDemoResult?.area || projectedResult?.area || ''" clip-rule="evenodd" fill-rule="evenodd" />
        </clipPath>
      </defs>
      <path v-if="projectedResult?.area" :d="projectedResult.area" class="real-analysis-area" fill-rule="evenodd" />
      <path v-for="(path, index) in projectedResult?.walkways ?? []" :key="`walk-${index}`" :d="path" class="real-analysis-walkway" />
      <path v-for="(path, index) in projectedResult?.gaps ?? []" :key="`gap-${index}`" :d="path" class="real-analysis-gap" />
      <g v-if="liveDemoResult?.area || projectedResult?.area" class="real-blind-zones"
        :clip-path="`url(#${blindClipId}-live)`">
        <path v-for="(zone, index) in projectedResult?.blindZones ?? []" :key="`blind-${index}`" :d="zone.d" class="real-blind-zone" :style="blindZoneStyle(zone.missingCategories)" fill-rule="evenodd" />
      </g>
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
          class="real-cpp-selected-route" :class="{ 'is-access': segment.access, 'is-baidu': segment.baidu }"
          :pathLength="segment.access ? undefined : 1" :style="{ '--route-delay': segment.delay }" />
      </g>
    </svg>
    <svg v-if="candidate && analysisComplete && analysisResult" class="real-poi-layer"
      :viewBox="`0 0 ${viewport.width} ${viewport.height}`" role="group" aria-label="步行范围内地点">
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
    <div v-if="analysisComplete && selectedPoi" class="real-poi-popover" role="status" @pointerdown.stop @click.stop>
      <button type="button" aria-label="关闭设施详情" @click.stop="dismissPoi">×</button>
      <strong>{{ selectedPoi.name }}</strong>
      <span>{{ poiCategoryStyles[selectedPoi.category].label }} · 百度地图地点</span>
      <small v-if="selectedPoiRoute?.destinationAccessMode !== 'estimated_straight_line'">地点入口请以现场情况为准。</small>
      <small v-if="selectedPoiRoute?.status === 'loading'">正在查询步行路线…</small>
      <template v-else-if="selectedPoiRoute?.status === 'ready'">
        <strong class="real-cpp-route-summary">{{ (selectedPoiRoute.travelTimeSeconds / 60).toFixed(1) }} 分钟 · {{ Math.round(selectedPoiRoute.lengthMeters) }} 米</strong>
        <small v-if="selectedPoiRoute.algorithm === 'baidu_walking'">百度步行参考路线 · {{ selectedPoiRoute.cacheSource === 'analysis' ? '复用当前分析路线' : selectedPoiRoute.cacheSource === 'shared_cache' ? '使用路线缓存' : '路线查询完成' }}</small>
        <small v-else>模拟步行路线 · 过街预计等待 {{ Math.round(selectedPoiRoute.crossingWaitSeconds) }} 秒</small>
        <small v-if="selectedPoiRoute.destinationAccessMode === 'estimated_straight_line'" class="real-cpp-route-warning">其中约 {{ Math.round(selectedPoiRoute.destinationAccessDistanceMeters) }} 米按直线估算，实际通行可能有差异。</small>
        <small v-if="!selectedPoiRoute.withinThreshold">这条路线超过 15 分钟。</small>
        <small v-if="selectedRouteClipped">本地底图仅显示已校准范围内的路径；百度预计耗时与距离为全程数据。</small>
      </template>
      <small v-else-if="selectedPoiRoute?.message">{{ selectedPoiRoute.message }}</small>
      <button v-if="selectedPoiRoute?.status === 'error'" type="button" class="real-cpp-route-retry" @click.stop="selectPoi(selectedPoi)">重试路线</button>
      <small v-if="selectedPoi.address">{{ selectedPoi.address }}</small>
    </div>

    <div v-if="analysisResult?.coordinateSystem === 'preview-local-v1'" class="real-demo-legend" aria-label="合成示意图例">
      <span><i :class="cppSyntheticResult ? 'legend-area' : 'legend-circle'"></i>{{ cppSyntheticResult ? '模拟步行范围' : '参考步行范围' }}</span>
      <span v-if="analysisMode === 'cpp'"><i class="legend-cpp-route"></i>{{ selectedPoiRoute?.status === 'ready' ? '选中地点的步行路线' : '点击地点查看路线' }}</span>
      <span v-else><i class="legend-route"></i>临时路网路线</span>
      <span v-if="selectedPoiRoute?.status === 'ready' && selectedRoutePaths.some(segment => segment.access)"><i class="legend-access"></i>估算接入 · 未核实</span>
    </div>
    <div ref="probeEl" class="real-map-probe" :class="{ visible: probeVisible, outside: !hoverInside }" aria-hidden="true">
      <span class="real-probe-ring"></span><small class="real-probe-caption">{{ mapState === 'ready' && boundaryState !== 'ready' ? '选区未就绪' : hoverInside ? '选起点' : '区外' }}</small>
    </div>
    <p v-if="notice" class="real-map-toast" role="status">{{ notice }}</p>
  </div>
</template>
