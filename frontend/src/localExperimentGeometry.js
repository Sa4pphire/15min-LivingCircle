import { fitLocalPoints, geometryToSvgPath } from "./mapGeometry.js";

export const localCategoryLabels = {
  shopping: "购物", healthcare: "医疗", education: "教育", recreation: "文体",
  transport: "交通", elderly_care: "养老", public_service: "公共服务",
};

export function localExperimentCategories(result) {
  return [...new Set([
    ...Object.keys(result?.metadata?.facilityInventoryStatusByCategory ?? {}),
    ...(result?.categorySegments?.features ?? []).map((feature) => feature.properties.category),
  ])];
}

// Drawing transform in the declared map coordinate system, not a datum
// conversion. It preserves the returned polylines and never creates areas.
export function localExperimentPlot(result, category, width = 1000, height = 360) {
  const walkways = result?.reachableWalkways?.features ?? [];
  if (!walkways.length) return null;
  const anchor = walkways[0].geometry.coordinates[0];
  const scaleX = 111320 * Math.cos(anchor[1] * Math.PI / 180);
  const toMeters = ([lng, lat]) => [
    (lng - anchor[0]) * scaleX, (anchor[1] - lat) * 111320,
  ];
  const points = walkways.flatMap((feature) => feature.geometry.coordinates.map(toMeters));
  const fit = fitLocalPoints(points, width, height, 0.13);
  const project = (point) => {
    const [x, y] = toMeters(point);
    return [fit.translateX + x * fit.scale, fit.translateY + y * fit.scale];
  };
  const unproject = ([x, y]) => [
    anchor[0] + (x - fit.translateX) / fit.scale / scaleX,
    anchor[1] - (y - fit.translateY) / fit.scale / 111320,
  ];
  const line = (feature, index) => ({
    id: `${feature.properties.edgeId}-${index}`,
    edgeId: feature.properties.edgeId,
    kind: feature.properties.kind,
    coordinates: feature.geometry.coordinates,
    path: geometryToSvgPath(feature.geometry, project),
    classification: feature.properties.classification,
  });
  return {
    project, unproject, pixelsPerMeter: fit.scale,
    coordType: result.metadata?.coordType ?? "bd09ll",
    walkways: walkways.map(line),
    selectableWalkways: walkways.filter((feature) =>
      ["sidewalk", "shared_way"].includes(feature.properties.kind)).map(line),
    segments: (result.categorySegments?.features ?? [])
      .filter((feature) => feature.properties.category === category).map(line),
  };
}

export function selectLocalWalkwayPoint(pixel, walkway, plot) {
  let nearest;
  let nearestDistance = Infinity;
  for (let index = 1; index < walkway.coordinates.length; index += 1) {
    const a = plot.project(walkway.coordinates[index - 1]);
    const b = plot.project(walkway.coordinates[index]);
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const squaredLength = dx * dx + dy * dy;
    const fraction = squaredLength ? Math.max(0, Math.min(1,
      ((pixel[0] - a[0]) * dx + (pixel[1] - a[1]) * dy) / squaredLength)) : 0;
    const point = [a[0] + fraction * dx, a[1] + fraction * dy];
    const distance = Math.hypot(point[0] - pixel[0], point[1] - pixel[1]);
    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearest = point;
    }
  }
  if (!nearest) return null;
  const [lng, lat] = plot.unproject(nearest);
  return { lng, lat, coordType: plot.coordType, originEdgeId: walkway.edgeId };
}
