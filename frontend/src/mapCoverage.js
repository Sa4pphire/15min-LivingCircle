// One camera contract for SVG and native-map limits, in local meter units.
export function boundsCorners(bounds) {
  return [[bounds.minX, bounds.minY], [bounds.maxX, bounds.minY],
    [bounds.maxX, bounds.maxY], [bounds.minX, bounds.maxY]];
}

export function containsBounds([x, y], bounds, tolerance = 1e-6) {
  return Number.isFinite(x) && Number.isFinite(y) &&
    x >= bounds.minX - tolerance && x <= bounds.maxX + tolerance &&
    y >= bounds.minY - tolerance && y <= bounds.maxY + tolerance;
}

export function constrainMapView(view, width, height, bounds) {
  const center = [(width / 2 - view.translateX) / view.scale,
    (height / 2 - view.translateY) / view.scale];
  const scale = Math.max(view.scale, width / (bounds.maxX - bounds.minX),
    height / (bounds.maxY - bounds.minY));
  const halfX = width / (2 * scale), halfY = height / (2 * scale);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const x = clamp(center[0], bounds.minX + halfX, bounds.maxX - halfX);
  const y = clamp(center[1], bounds.minY + halfY, bounds.maxY - halfY);
  return { scale, translateX: width / 2 - x * scale, translateY: height / 2 - y * scale };
}

export function metricScale(metersPerPixel, maxWidth = 110) {
  if (!Number.isFinite(metersPerPixel) || metersPerPixel <= 0) return null;
  const maximum = metersPerPixel * maxWidth;
  const power = 10 ** Math.floor(Math.log10(maximum));
  const meters = [5, 2, 1].map(n => n * power).find(n => n <= maximum) ?? power / 2;
  return { meters, pixels: meters / metersPerPixel,
    label: meters >= 1000 ? `${Number((meters / 1000).toPrecision(3))} 公里` : `${Number(meters.toPrecision(3))} 米` };
}

export function coverageCorrection(points, bounds) {
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const west = Math.min(...xs), east = Math.max(...xs), north = Math.min(...ys), south = Math.max(...ys);
  return { zoomRatio: Math.max((east - west) / (bounds.maxX - bounds.minX),
    (south - north) / (bounds.maxY - bounds.minY)),
    dx: west < bounds.minX ? bounds.minX - west : east > bounds.maxX ? bounds.maxX - east : 0,
    dy: north < bounds.minY ? bounds.minY - north : south > bounds.maxY ? bounds.maxY - south : 0 };
}
