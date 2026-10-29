// Export the current calculation only; never recompute routes or smoothing.
import { getActiveRegion } from "./regionLoader.js";

function escapeXml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
  })[character]);
}

function geometryPath(geometry) {
  // Keep full coordinate precision. The SVG group transform controls the view;
  // no simplification or coordinate rounding changes the certified contour.
  return geometry.coordinates.map(polygon => polygon.map(ring =>
    ring.map(([x, y], index) => `${index ? "L" : "M"}${x} ${y}`).join(" ") + " Z",
  ).join(" ")).join(" ");
}

function reviewData(result) {
  if (result?.source !== "synthetic-cpp-engine" ||
      result.displayArea?.geometry?.type !== "MultiPolygon") {
    throw new Error("请先完成一次 C++ 路网计算");
  }
  const region = getActiveRegion();
  const boundarySmoothing = result.boundarySmoothing?.mode === "unconstrained"
    ? result.boundarySmoothing : null;
  const smoothedGeometry = boundarySmoothing && result.smoothedDisplayArea?.geometry?.type === "MultiPolygon"
    ? result.smoothedDisplayArea.geometry : null;
  return {
    schemaVersion: 2,
    exportedAt: new Date().toISOString(),
    analysisId: result.analysisId ?? null,
    geometryRevision: result.geometryRevision,
    regionId: region.id,
    coordinateSystem: result.coordinateSystem,
    coordinateUnit: "meter",
    axes: "east-south",
    geographicOrigin: region.geographicOrigin || region.originWgs84 || region.originBd09,
    geographicCoordType: region.geographicCoordType || "wgs84ll",
    origin: result.origin,
    inputParameters: boundarySmoothing?.inputParameters ?? null,
    rawGeometry: result.displayArea.geometry,
    smoothedGeometry,
    smoothingStatus: smoothedGeometry ? boundarySmoothing.status : "unavailable",
    receivedAlgorithmVersion: result.boundarySmoothing?.algorithmVersion ?? null,
    reachableSegments: result.routeSegments.map(segment => segment.points),
    frontierPoints: result.boundaryReviewFrontierPoints ?? [],
    boundarySmoothing,
    note: "Coordinates are the current preview meter frame, reconstructed from map coordinates. " +
      "Deviation statistics refer to the raw C++ display contour, not real-world walking accuracy. " +
      "Smoothing has no accuracy or topology constraints; unavailable area metrics and missing smoothed geometry are null.",
  };
}

function comparisonSvg(data) {
  if (!data.smoothedGeometry) {
    throw new Error("当前计算结果缺少新版平滑数据，无法导出对照 SVG；请加载新后端后重新计算。数据 JSON 仍可导出用于排查。");
  }
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  const geometries = [data.rawGeometry, data.smoothedGeometry];
  for (const geometry of geometries) for (const polygon of geometry.coordinates) {
    for (const ring of polygon) for (const [x, y] of ring) {
      bounds[0] = Math.min(bounds[0], x); bounds[1] = Math.min(bounds[1], y);
      bounds[2] = Math.max(bounds[2], x); bounds[3] = Math.max(bounds[3], y);
    }
  }
  if (!bounds.every(Number.isFinite)) throw new Error("当前等时圈为空，无法导出对照 SVG");
  const scale = Math.min(650 / Math.max(1, bounds[2] - bounds[0]),
    430 / Math.max(1, bounds[3] - bounds[1]));
  const centerX = (bounds[0] + bounds[2]) / 2;
  const centerY = (bounds[1] + bounds[3]) / 2;
  const rawPath = geometryPath(data.rawGeometry);
  const routes = data.reachableSegments.map(points =>
    points.map(([x, y], index) => `${index ? "L" : "M"}${x} ${y}`).join(" "),
  ).join(" ");
  const statusLabels = { raw: "原始", smoothed: "已平滑", unchanged: "未变化",
    raw_fallback: "已回退", unavailable: "未提供" };
  const panels = [
    { title: "原始 C++ 轮廓", geometry: data.rawGeometry, metadata: null },
    { title: "平滑轮廓", geometry: data.smoothedGeometry, metadata: data.boundarySmoothing },
  ];
  const pageHeight = 130 + Math.ceil(panels.length / 2) * 540;
  const body = panels.map((panel, index) => {
    const x = 20 + index % 2 * 710;
    const y = 100 + Math.floor(index / 2) * 540;
    const metadata = panel.metadata;
    const status = metadata?.status ?? "raw";
    const deviation = Number.isFinite(metadata?.boundaryDeviationUpperBoundMeters)
      ? `偏移上界 ${metadata.boundaryDeviationUpperBoundMeters.toFixed(3)} m` : "偏差未计算";
    const difference = Number.isFinite(metadata?.symmetricDifferenceAreaSquareMeters)
      ? `面积对称差 ${metadata.symmetricDifferenceAreaSquareMeters.toFixed(3)} m²` : "面积未计算";
    const details = metadata
      ? `${statusLabels[status] ?? status} · ${deviation} · ${difference}`
      : "同次计算基线；其余面板的灰色虚线均为这条原始边界";
    const fallback = metadata?.fallbackReason ? ` · ${metadata.fallbackReason}` : "";
    const points = data.frontierPoints.map(([px, py]) =>
      `<circle cx="${px}" cy="${py}" r="${1.8 / scale}" fill="#c26b3d"/>`,
    ).join("");
    return `<g transform="translate(${x} ${y})">
      <rect width="690" height="520" rx="10" fill="white" stroke="#d1ded9"/>
      <text x="18" y="30" font-size="18" font-weight="600">${escapeXml(panel.title)}</text>
      <text x="18" y="52" font-size="11" fill="#52625c">${escapeXml(details + fallback)}</text>
      <svg x="15" y="66" width="660" height="440" viewBox="0 0 660 440">
        <g transform="translate(${330 - centerX * scale} ${220 - centerY * scale}) scale(${scale})">
          <path d="${geometryPath(panel.geometry)}" fill="#d4e8df" fill-rule="evenodd" stroke="#267368" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
          <path d="${routes}" fill="none" stroke="#93b6a9" stroke-width="0.6" vector-effect="non-scaling-stroke"/>
          ${index ? `<path d="${rawPath}" fill="none" stroke="#67716c" stroke-width="0.8" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>` : ""}
          ${points}
          <circle cx="${data.origin.x}" cy="${data.origin.y}" r="${4 / scale}" fill="#267368" stroke="white" stroke-width="1" vector-effect="non-scaling-stroke"/>
        </g>
      </svg>
    </g>`;
  }).join("\n");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="${pageHeight}" viewBox="0 0 1440 ${pageHeight}">
    <title>等时圈边界去噪同次计算对照</title>
    <metadata>${escapeXml(JSON.stringify({ analysisId: data.analysisId, regionId: data.regionId,
      exportedAt: data.exportedAt, boundarySmoothing: data.boundarySmoothing }))}</metadata>
    <rect width="1440" height="${pageHeight}" fill="#f0f5f2"/>
    <g font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif" fill="#203d33">
      <text x="24" y="36" font-size="24" font-weight="600">等时圈边界去噪对照</text>
      <text x="24" y="63" font-size="13">同一范围与比例 · 灰色虚线为原始边界 · 棕色点为时间边界样点 · 单位：米</text>
      <text x="24" y="83" font-size="12" fill="#52625c">偏差为相对原始面的事后统计；平滑未设置精度与拓扑约束。</text>
      ${body}
      <text x="24" y="${pageHeight - 16}" font-size="11" fill="#52625c">${escapeXml(data.regionId)} · ${escapeXml(data.analysisId ?? "")} · ${escapeXml(data.exportedAt)}</text>
    </g>
  </svg>`;
}

export function downloadBoundarySmoothingReview(result, format = "svg") {
  const data = reviewData(result);
  const isJson = format === "json";
  const content = isJson ? JSON.stringify(data, null, 2) : comparisonSvg(data);
  const url = URL.createObjectURL(new Blob([content], {
    type: isJson ? "application/json;charset=utf-8" : "image/svg+xml;charset=utf-8",
  }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `boundary-smoothing-${data.analysisId || "current"}.${isJson ? "json" : "svg"}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
