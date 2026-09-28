// The local road-network experiment is independent from the 15-minute report.
// Keep the declared coordinate system; this independent SVG does not convert it.
const VALID_COORDINATE_TYPES = new Set(["bd09ll", "wgs84ll"]);
const VALID_CLASSIFICATIONS = new Set([
  "covered", "candidate_uncovered", "unknown",
]);

export function normalizeLocalExperimentResult(result) {
  if (result?.mode !== "local_experiment" ||
      result.notForMainReport !== true ||
      result.thresholdSeconds !== 180 ||
      !VALID_COORDINATE_TYPES.has(result.metadata?.coordType) ||
      !["manual", "synthetic"].includes(result.metadata?.networkSource) ||
      result.reachableWalkways?.type !== "FeatureCollection" ||
      result.categorySegments?.type !== "FeatureCollection" ||
      !Array.isArray(result.reachableWalkways.features) ||
      !Array.isArray(result.categorySegments.features)) {
    throw new Error("INVALID_LOCAL_EXPERIMENT_RESULT");
  }
  for (const feature of [
    ...result.reachableWalkways.features,
    ...result.categorySegments.features,
  ]) {
    if (feature?.type !== "Feature" || typeof feature.properties?.edgeId !== "string" ||
        feature.geometry?.type !== "LineString" ||
        !Array.isArray(feature.geometry.coordinates) ||
        feature.geometry.coordinates.length < 2 ||
        !feature.geometry.coordinates.every((point) =>
          Array.isArray(point) && point.length === 2 && point.every(Number.isFinite) &&
          Math.abs(point[0]) <= 180 && Math.abs(point[1]) < 90)) {
      throw new Error("INVALID_LOCAL_EXPERIMENT_GEOMETRY");
    }
  }
  if (result.categorySegments.features.some((feature) =>
    !VALID_CLASSIFICATIONS.has(feature.properties?.classification) ||
    typeof feature.properties?.category !== "string")) {
    throw new Error("INVALID_LOCAL_EXPERIMENT_CLASSIFICATION");
  }
  return { ...result, source: "local-experiment", coordinateSystem: result.metadata.coordType };
}

async function responseJson(response) {
  const data = await response.json();
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(detail?.code ? `${detail.code}: ${detail.message ?? ""}` :
      `LOCAL_EXPERIMENT_HTTP_${response.status}`);
  }
  return data;
}

export async function requestLocalExperiment({ candidate, originEdgeId, includePois = true, refreshPois = false },
  { signal, fetchImpl = fetch } = {}) {
  if (!VALID_COORDINATE_TYPES.has(candidate?.coordType) ||
      !Number.isFinite(candidate.lng) || !Number.isFinite(candidate.lat) ||
      Math.abs(candidate.lng) > 180 || Math.abs(candidate.lat) >= 90) {
    throw new Error("LOCAL_EXPERIMENT_REQUIRES_EXPLICIT_COORDINATE_TYPE");
  }
  const payload = {
    center: { lng: candidate.lng, lat: candidate.lat, coordType: candidate.coordType },
    includePois, refreshPois,
  };
  if (originEdgeId?.trim()) payload.originEdgeId = originEdgeId.trim();
  const accepted = await responseJson(await fetchImpl("/api/v1/local-experiments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  }));
  if (!accepted.analysisId) throw new Error("MISSING_LOCAL_EXPERIMENT_ID");
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException("Analysis cancelled", "AbortError");
    const state = await responseJson(await fetchImpl(
      `/api/v1/local-experiments/${encodeURIComponent(accepted.analysisId)}`,
      { signal }));
    if (state.status === "completed") {
      const result = normalizeLocalExperimentResult(state.result);
      if (result.coordinateSystem !== candidate.coordType) {
        throw new Error("LOCAL_EXPERIMENT_COORDINATE_TYPE_MISMATCH");
      }
      return result;
    }
    if (state.status === "failed") {
      const detail = state.error;
      throw new Error(typeof detail === "string" ? detail :
        detail?.code ? `${detail.code}: ${detail.message ?? ""}` :
          "LOCAL_EXPERIMENT_FAILED");
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("LOCAL_EXPERIMENT_TIMEOUT");
}
