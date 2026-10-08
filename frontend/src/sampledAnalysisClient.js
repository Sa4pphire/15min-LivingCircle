// 第一套算法：调用百度采样 + Python 插值接口，并整理地图显示数据。
function normalizeSampledReport(report, candidate) {
  if (report?.sourceMode !== "baidu-sampled" ||
      report.coordinateSystem !== "bd09ll" ||
      report.isochrone?.type !== "MultiPolygon" ||
      report.isochroneMeters?.type !== "MultiPolygon") {
    throw new Error("INVALID_SAMPLED_REPORT");
  }

  // 后端局部坐标是“向北为正”，前端备用 SVG 地图是“向下为正”。
  const fallbackGeometry = {
    type: "MultiPolygon",
    coordinates: report.isochroneMeters.coordinates.map((polygon) =>
      polygon.map((ring) =>
        ring.map(([x, y]) => [
          candidate.local.x + x,
          candidate.local.y - y,
        ]))),
  };

  // 只保留至少含两个 BD-09 点的真实步行路线段，交给地图组件绘制。
  const routeSegments = (report.routeSegments ?? [])
    .filter((segment) => Array.isArray(segment?.points))
    .map((segment) => ({
      ...segment,
      points: segment.points.filter((point) =>
        Array.isArray(point) && point.length >= 2 &&
        Number.isFinite(point[0]) && Number.isFinite(point[1])),
    }))
    .filter((segment) => segment.points.length >= 2);
  const samplingRouteSegments = (report.samplingRouteSegments ?? [])
    .filter((segment) => Array.isArray(segment?.points))
    .map((segment) => ({
      ...segment,
      points: segment.points.filter((point) =>
        Array.isArray(point) && point.length >= 2 &&
        Number.isFinite(point[0]) && Number.isFinite(point[1])),
    }))
    .filter((segment) => segment.points.length >= 2);
  const routeCount = report.routeCount ?? new Set(
    routeSegments.map((segment) => segment.poiUid).filter(Boolean),
  ).size;
  const poiFacilities = (report.facilities?.features ?? []).flatMap((feature) => {
    const coordinates = feature.geometry?.coordinates;
    const properties = feature.properties ?? {};
    if (feature.geometry?.type !== "Point" || !Array.isArray(coordinates) ||
        coordinates.length !== 2 || !coordinates.every(Number.isFinite) ||
        !properties.uid || !properties.category) return [];
    const localPoint = [
      candidate.local.x + (coordinates[0] - candidate.lng) *
        111320 * Math.cos(candidate.lat * Math.PI / 180),
      candidate.local.y - (coordinates[1] - candidate.lat) * 111320,
    ];
    return [{ id: `sampled:${properties.category}:${properties.uid}`,
      uid: properties.uid, name: properties.name ?? "服务点",
      address: properties.address ?? "", category: properties.category,
      categories: [properties.category], bd09: [...coordinates], point: localPoint,
      insideDisplayPolygon: true, modelReachable: null }];
  });

  return {
    source: "baidu-sampled-idw",
    coordinateSystem: "bd09ll",
    displayArea: { type: "polygon", geometry: report.isochrone },
    fallbackDisplayArea: { type: "polygon", geometry: fallbackGeometry },
    routeSegments,
    samplingRouteSegments,
    blindZones: report.blindZones ?? { type: "FeatureCollection", features: [] },
    blindZoneStatus: report.blindZoneStatus ?? report.blindZones?.properties?.status ?? "unknown",
    blindZoneResolutionMeters: report.blindZoneResolutionMeters
      ?? report.blindZones?.properties?.resolutionMeters ?? null,
    facilities: report.facilities ?? {
      type: "FeatureCollection",
      features: [],
    },
    poiFacilities,
    routeFailures: report.routeFailures ?? [],
    accessLink: null,
    summary: {
      sampleCount: report.durationSamples?.length ?? 0,
      routeCount,
      routeSegmentCount: routeSegments.length,
      poiRouteCount: routeCount,
      samplingRouteCount: report.samplingRouteCount ?? new Set(
        samplingRouteSegments.map((segment) => segment.sampleIndex).filter(Number.isFinite),
      ).size,
      samplingRouteSegmentCount: samplingRouteSegments.length,
      thresholdSeconds: report.thresholdSeconds,
      approximate: report.approximate,
    },
  };
}

async function readResponse(response) {
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail?.message || `ANALYSIS_HTTP_${response.status}`);
  }
  return data;
}

// 提交一个选点，然后轮询到“完成”或“失败”。
export async function requestSampledMapAnalysis(
  candidate,
  { signal, fetchImpl = fetch } = {},
) {
  if (![candidate?.lng, candidate?.lat,
    candidate?.local?.x, candidate?.local?.y].every(Number.isFinite)) {
    throw new Error("INVALID_ANALYSIS_ORIGIN");
  }

  const accepted = await readResponse(await fetchImpl("/api/v1/sampled-analyses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      center: {
        lng: candidate.lng,
        lat: candidate.lat,
        coordType: candidate.coordType,
      },
      minutes: 15,
    }),
    signal,
  }));

  if (!accepted.analysisId) throw new Error("MISSING_ANALYSIS_ID");

  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException("Analysis cancelled", "AbortError");

    const state = await readResponse(await fetchImpl(
      `/api/v1/sampled-analyses/${encodeURIComponent(accepted.analysisId)}`,
      { signal },
    ));

    if (state.status === "completed") {
      return normalizeSampledReport(state.result, candidate);
    }
    if (state.status === "failed") {
      throw new Error(state.error || "SAMPLED_ANALYSIS_FAILED");
    }

    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  throw new Error("SAMPLED_ANALYSIS_TIMEOUT");
}
