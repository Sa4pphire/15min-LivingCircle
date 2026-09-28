import { fitLocalPoints } from "./mapGeometry.js";
import { visiblePois } from "./poiFacilities.js";

const validPoint = point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite);

// A result overview is separate from the three user-selected zoom tiers.
// Fit the actual C++ geometry, not the entire saved map's (larger) extent.
export function cppOverviewPoints(result) {
  const geometry = result?.displayArea?.geometry;
  if (geometry?.type !== "MultiPolygon" || !Array.isArray(geometry.coordinates)) return [];
  const points = geometry.coordinates.flat(2).filter(validPoint);
  const origin = [result.origin?.x, result.origin?.y];
  if (validPoint(origin)) points.push(origin);
  points.push(...visiblePois(result).map(poi => poi.point).filter(validPoint));
  return points;
}

export function cppOverviewInsets(width, height) {
  return { top: Math.min(220, height * .4), right: Math.min(95, width * .28),
    bottom: Math.min(90, height * .18), left: 16 };
}

export function cppOverviewFit(result, width, height) {
  const points = cppOverviewPoints(result);
  if (!points.length) return null;
  const inset = cppOverviewInsets(width, height);
  const fit = fitLocalPoints(points, Math.max(1, width - inset.left - inset.right),
    Math.max(1, height - inset.top - inset.bottom), .08);
  return { ...fit, translateX: fit.translateX + inset.left, translateY: fit.translateY + inset.top };
}
