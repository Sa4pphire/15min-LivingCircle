// Geometry shared by the no-AK SVG preview and the Baidu/SVG overlay.

export function pointInRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi);
    const onSegment = Math.abs(cross) < 1e-10 &&
      x >= Math.min(xi, xj) - 1e-10 && x <= Math.max(xi, xj) + 1e-10 &&
      y >= Math.min(yi, yj) - 1e-10 && y <= Math.max(yi, yj) + 1e-10;
    if (onSegment) return true;
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function pointInPolygon(point, rings) {
  return Boolean(rings?.length) && pointInRing(point, rings[0]) &&
    !rings.slice(1).some((hole) => pointInRing(point, hole));
}

export function wgsToLocal([lng, lat], [originLng, originLat]) {
  return [
    (lng - originLng) * 111320 * Math.cos(originLat * Math.PI / 180),
    (originLat - lat) * 111320,
  ];
}

export function localToWgs([x, y], [originLng, originLat]) {
  return [
    originLng + x / (111320 * Math.cos(originLat * Math.PI / 180)),
    originLat - y / 111320,
  ];
}

// The four-road polygon limits origin selection, not the map or the reachability result.
// At 1.3 m/s, 15 minutes covers at most 1,170 m before crossing delays.
export const DISPLAY_PADDING_METERS = 1300;

export function expandedLocalBounds(points, padding = DISPLAY_PADDING_METERS) {
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const minX = Math.min(...xs) - padding;
  const maxX = Math.max(...xs) + padding;
  const minY = Math.min(...ys) - padding;
  const maxY = Math.max(...ys) + padding;
  return [[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY]];
}

export function fitLocalPoints(points, width, height, inset = 0.08) {
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const margin = Math.min(width, height) * inset;
  const scale = Math.min(
    (width - 2 * margin) / Math.max(1, maxX - minX),
    (height - 2 * margin) / Math.max(1, maxY - minY),
  );
  const translateX = width / 2 - (minX + maxX) / 2 * scale;
  const translateY = height / 2 - (minY + maxY) / 2 * scale;
  return { scale, translateX, translateY };
}

export function geometryToSvgPath(geometry, project) {
  if (!geometry) return "";
  const line = (coordinates, close = false) => coordinates.map((point, index) => {
    const [x, y] = project(point);
    return `${index ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ") + (close ? " Z" : "");
  switch (geometry.type) {
    case "LineString": return line(geometry.coordinates);
    case "MultiLineString": return geometry.coordinates.map((item) => line(item)).join(" ");
    case "Polygon": return geometry.coordinates.map((ring) => line(ring, true)).join(" ");
    case "MultiPolygon": return geometry.coordinates.flatMap((polygon) =>
      polygon.map((ring) => line(ring, true))).join(" ");
    default: return "";
  }
}
