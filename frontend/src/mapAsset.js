// Fixed geometry is prepared by Python, never converted on page load.
import { expandedLocalBounds, wgsToLocal } from "./mapGeometry.js";

function validPoint(point) {
  return Array.isArray(point) && point.length === 2 && point.every(Number.isFinite) &&
    Math.abs(point[0]) <= 180 && Math.abs(point[1]) < 90;
}

// Read independently: an invalid overlay must not hide a valid base map.
export function preparedMapCenter(asset) {
  return asset?.schemaVersion === 1 && asset?.coordType === "bd09ll" && validPoint(asset.centerBd09)
    ? asset.centerBd09 : null;
}

export function preparedMapGeometry(asset, { ringWgs84, originWgs84, paddingMeters }) {
  const input = asset?.input;
  if (!preparedMapCenter(asset) ||
    JSON.stringify(input?.boundaryCoordinatesWgs84) !== JSON.stringify(ringWgs84) ||
    JSON.stringify(input?.originWgs84) !== JSON.stringify(originWgs84) ||
    input?.displayPaddingMeters !== paddingMeters) {
    throw new Error("本地 BD-09 边界与当前选区不一致，请重新生成地图数据");
  }
  const geometry = asset.boundary?.geometry;
  const ring = geometry?.coordinates?.[0];
  const corners = asset.displayCornersBd09;
  if (geometry?.type !== "Polygon" || geometry.coordinates.length !== 1 ||
    !Array.isArray(ring) || ring.length !== ringWgs84.length || !ring.every(validPoint) ||
    JSON.stringify(ring[0]) !== JSON.stringify(ring.at(-1)) ||
    !Array.isArray(corners) || corners.length !== 4 || !corners.every(validPoint)) {
    throw new Error("本地 BD-09 边界数据不完整，请重新生成地图数据");
  }
  const dx = [corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]];
  const dy = [corners[3][0] - corners[0][0], corners[3][1] - corners[0][1]];
  if (Math.abs(dx[0] * dy[1] - dx[1] * dy[0]) < 1e-12) {
    throw new Error("本地 BD-09 展示范围无效，请重新生成地图数据");
  }
  const alignment = createLocalBd09Alignment(asset);
  const localCorners = expandedLocalBounds(ringWgs84.map(point => wgsToLocal(point, originWgs84)), paddingMeters);
  const center = alignment.toBd09([0, 0]);
  if (!localCorners.every(point => alignment.toBd09(point)) || !center ||
    Math.hypot(center[0] - asset.centerBd09[0], center[1] - asset.centerBd09[1]) > 1e-5) {
    throw new Error("本地 BD-09 路网校准网格与选区不一致，请重新生成地图数据");
  }
  return { ring, corners, alignment };
}

// WGS-84 -> BD-09 is not affine across a five-kilometre viewport. Interpolate
// locally between official anchors, and invert the SAME cells for map clicks.
export function createLocalBd09Alignment(asset) {
  const grid = asset?.alignment;
  const { columns, rows, stepMeters: step, minLocalMeters: minimum, pointsBd09: points } = grid ?? {};
  if (grid?.kind !== "bilinear_grid" || grid.localAxis !== "east-south" ||
    !Number.isInteger(columns) || !Number.isInteger(rows) || columns < 2 || rows < 2 ||
    columns > 128 || rows > 128 || !Number.isFinite(step) || step <= 0 || step > 250 ||
    !Array.isArray(minimum) || minimum.length !== 2 || !minimum.every(Number.isFinite) ||
    !Array.isArray(points) || points.length !== columns * rows || !points.every(validPoint)) {
    throw new Error("本地 BD-09 路网校准网格无效，请重新生成地图数据");
  }
  const anchor = (column, row) => points[row * columns + column];
  const cell = (column, row, u, v) => {
    const a = anchor(column, row), b = anchor(column + 1, row);
    const c = anchor(column, row + 1), d = anchor(column + 1, row + 1);
    const position = [0, 1].map(axis =>
      (1 - v) * ((1 - u) * a[axis] + u * b[axis]) + v * ((1 - u) * c[axis] + u * d[axis]));
    const du = [0, 1].map(axis => (1 - v) * (b[axis] - a[axis]) + v * (d[axis] - c[axis]));
    const dv = [0, 1].map(axis => (1 - u) * (c[axis] - a[axis]) + u * (d[axis] - b[axis]));
    return { position, du, dv, determinant: du[0] * dv[1] - du[1] * dv[0] };
  };
  const bounds = [];
  for (let row = 0; row < rows - 1; row += 1) {
    for (let column = 0; column < columns - 1; column += 1) {
      const corners = [anchor(column, row), anchor(column + 1, row),
        anchor(column, row + 1), anchor(column + 1, row + 1)];
      for (const [u, v] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
        if (cell(column, row, u, v).determinant >= -1e-12) {
          throw new Error("本地 BD-09 路网校准网格退化，请重新生成地图数据");
        }
      }
      bounds.push({ column, row, west: Math.min(...corners.map(p => p[0])),
        east: Math.max(...corners.map(p => p[0])), south: Math.min(...corners.map(p => p[1])),
        north: Math.max(...corners.map(p => p[1])) });
    }
  }
  function toBd09(point) {
    if (!Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite)) return null;
    let x = (point[0] - minimum[0]) / step, y = (point[1] - minimum[1]) / step;
    if (x < -1e-9 || y < -1e-9 || x > columns - 1 + 1e-9 || y > rows - 1 + 1e-9) return null;
    x = Math.max(0, Math.min(columns - 1, x));
    y = Math.max(0, Math.min(rows - 1, y));
    const column = Math.min(columns - 2, Math.floor(x)), row = Math.min(rows - 2, Math.floor(y));
    return cell(column, row, x - column, y - row).position;
  }
  function toLocal(point) {
    if (!validPoint(point)) return null;
    for (const box of bounds) {
      if (point[0] < box.west - 1e-10 || point[0] > box.east + 1e-10 ||
        point[1] < box.south - 1e-10 || point[1] > box.north + 1e-10) continue;
      let u = 0.5, v = 0.5;
      for (let iteration = 0; iteration < 10; iteration += 1) {
        const { position, du, dv, determinant } = cell(box.column, box.row, u, v);
        const dx = position[0] - point[0], dy = position[1] - point[1];
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 1e-11) break;
        u -= (dx * dv[1] - dy * dv[0]) / determinant;
        v -= (du[0] * dy - du[1] * dx) / determinant;
      }
      const actual = cell(box.column, box.row, u, v).position;
      if (u >= -1e-7 && u <= 1 + 1e-7 && v >= -1e-7 && v <= 1 + 1e-7 &&
        Math.hypot(actual[0] - point[0], actual[1] - point[1]) < 1e-10) {
        return [minimum[0] + (box.column + Math.max(0, Math.min(1, u))) * step,
          minimum[1] + (box.row + Math.max(0, Math.min(1, v))) * step];
      }
    }
    return null; // Never extrapolate or treat a BD-09 point as WGS-84.
  }
  return { toBd09, toLocal };
}

// Long straight WGS/local edges need intermediate samples: a nonlinear datum
// transform does not map them to one straight BD-09 line between two endpoints.
export function sampleLocalPath(points, spacingMeters = 125) {
  if (!Array.isArray(points) || points.length < 2 || !Number.isFinite(spacingMeters) || spacingMeters <= 0) {
    return points;
  }
  const sampled = [points[0]];
  for (let index = 1; index < points.length; index += 1) {
    const a = points[index - 1], b = points[index];
    const count = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / spacingMeters));
    for (let part = 1; part <= count; part += 1) {
      sampled.push(part === count ? b : [a[0] + (b[0] - a[0]) * part / count,
        a[1] + (b[1] - a[1]) * part / count]);
    }
  }
  return sampled;
}

export function sampleLocalArea(geometry) {
  if (geometry?.type !== "MultiPolygon") return geometry;
  return { ...geometry, coordinates: geometry.coordinates.map(polygon => polygon.map(ring => sampleLocalPath(ring))) };
}
