// Synthetic Python -> C++ -> frontend integration path. The API
// reports WGS-84 for this fixture; convert it back to the SVG preview's local
// south-positive coordinates. Nothing here claims verified pedestrian access.
import { localToWgs, wgsToLocal } from "./mapGeometry.js";
import { poiCandidates } from "./poiFacilities.js";

export const PREVIEW_ORIGIN_WGS84 = [121.505, 31.333];

function localizeGeometry(geometry) {
  if (geometry?.type !== "MultiPolygon" || !Array.isArray(geometry.coordinates)) {
    throw new Error("INVALID_CPP_DISPLAY_GEOMETRY");
  }
  return {
    type: "MultiPolygon",
    coordinates: geometry.coordinates.map((polygon) => polygon.map((ring) =>
      ring.map((point) => wgsToLocal(point, PREVIEW_ORIGIN_WGS84)))),
  };
}

function localizeFeatureCollection(collection) {
  if (collection?.type !== "FeatureCollection" || !Array.isArray(collection.features)) {
    return { type: "FeatureCollection", features: [] };
  }
  return {
    type: "FeatureCollection",
    features: collection.features.flatMap((feature, index) => {
      if (!feature?.geometry || !["Polygon", "MultiPolygon", "LineString"].includes(feature.geometry.type)) {
        return [];
      }
      const geometry = feature.geometry.type === "MultiPolygon"
        ? localizeGeometry(feature.geometry)
        : feature.geometry.type === "Polygon"
          ? { type: "Polygon", coordinates: feature.geometry.coordinates.map(ring =>
              ring.map(point => wgsToLocal(point, PREVIEW_ORIGIN_WGS84))) }
          : { type: "LineString", coordinates: feature.geometry.coordinates.map(point =>
              wgsToLocal(point, PREVIEW_ORIGIN_WGS84)) };
      return [{ ...feature, id: feature.id ?? `feature-${index}`, geometry }];
    }),
  };
}

export function normalizeCppReport(report, origin) {
  if (report?.metadata?.networkSource !== "synthetic" ||
      report.metadata.coordType !== "wgs84ll" ||
      !Array.isArray(report?.reachableWalkways?.features)) {
    throw new Error("NOT_SYNTHETIC_WGS84_REPORT");
  }
  const routeSegments = [];
  for (const feature of report.reachableWalkways.features) {
    if (feature?.geometry?.type !== "LineString" ||
        !Array.isArray(feature.geometry.coordinates)) continue;
    const points = feature.geometry.coordinates.map((point) =>
      wgsToLocal(point, PREVIEW_ORIGIN_WGS84));
    for (let index = 1; index < points.length; index += 1) {
      const startProgress = Math.min(1, Math.hypot(
        points[index - 1][0] - origin.x, points[index - 1][1] - origin.y,
      ) / 1170);
      routeSegments.push({
        id: `${feature.properties?.edgeId ?? "walk"}:${index - 1}`,
        points: [points[index - 1], points[index]],
        // Visual sequencing only; the C++ result does not expose per-edge time.
        startProgress,
      });
    }
  }
  const accessMeters = report.metadata.originSnapMeters;
  const snappedOrigin = report.metadata.snappedOrigin;
  const accessLink = Number.isFinite(accessMeters) && accessMeters > 0.01 &&
    Array.isArray(snappedOrigin) && snappedOrigin.length === 2
    ? {
        points: [[origin.x, origin.y], wgsToLocal(snappedOrigin, PREVIEW_ORIGIN_WGS84)],
        length: accessMeters,
        estimatedSeconds: report.metadata.originAccessSeconds,
        verified: false,
      }
    : null;
  return {
    schemaVersion: 1,
    source: "synthetic-cpp-engine",
    coordinateSystem: "preview-local-v1",
    origin: { x: origin.x, y: origin.y },
    displayArea: { type: "polygon", geometry: localizeGeometry(report.isochrone?.geometry) },
    blindZones: localizeFeatureCollection(report.blindZones),
    blindZoneWalkways: localizeFeatureCollection(report.blindZoneWalkways),
    routeSegments,
    accessLink,
    poiFacilities: poiCandidates(report),
    poiCategories: report.poiCategories ?? [],
    poiInfo: report.metadata.poi ?? null,
    summary: {
      routeSegmentCount: routeSegments.length,
      originSnapMeters: accessMeters,
      originAccessSeconds: report.metadata.originAccessSeconds,
      warnings: report.warnings ?? [],
    },
  };
}

async function responseJson(response) {
  const data = await response.json();
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(detail?.code ? `${detail.code}: ${detail.message}` :
      `ANALYSIS_HTTP_${response.status}`);
  }
  return data;
}

export async function requestCppMapAnalysis({ origin, includePois = true, refreshPois = false }, { signal, fetchImpl = fetch } = {}) {
  if (!Number.isFinite(origin?.x) || !Number.isFinite(origin?.y)) {
    throw new Error("INVALID_ANALYSIS_ORIGIN");
  }
  const [lng, lat] = localToWgs([origin.x, origin.y], PREVIEW_ORIGIN_WGS84);
  const accepted = await responseJson(await fetchImpl("/api/v1/synthetic-analyses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ center: { lng, lat, coordType: "wgs84ll" }, minutes: 15, includePois, refreshPois }),
    signal,
  }));
  if (!accepted.analysisId) throw new Error("MISSING_ANALYSIS_ID");
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException("Analysis cancelled", "AbortError");
    const state = await responseJson(await fetchImpl(
      `/api/v1/analyses/${encodeURIComponent(accepted.analysisId)}`, { signal }));
    if (state.status === "completed") return normalizeCppReport(state.result, origin);
    if (state.status === "failed") throw new Error(state.error || "CPP_ANALYSIS_FAILED");
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("CPP_ANALYSIS_TIMEOUT");
}
