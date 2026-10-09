export function pathBounds(points, southPositive = false) {
  const bounds = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const point of points) {
    const y = southPositive ? -point[1] : point[1];
    bounds.minX = Math.min(bounds.minX, point[0]); bounds.maxX = Math.max(bounds.maxX, point[0]);
    bounds.minY = Math.min(bounds.minY, y); bounds.maxY = Math.max(bounds.maxY, y);
  }
  return bounds;
}

export function intersectsBounds(bounds, viewport) {
  return !bounds || !(bounds.maxX < viewport.minX || bounds.minX > viewport.maxX ||
    bounds.maxY < viewport.minY || bounds.minY > viewport.maxY);
}

export function backdropBounds(d) {
  if (/[^MLZ\d\s.,+\-eE]/.test(d)) return null; // Do not guess bounds for curves or relative paths.
  const numbers = d.match(/[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?/gi)?.map(Number) ?? [];
  const points = [];
  for (let i = 0; i + 1 < numbers.length; i += 2) points.push([numbers[i], numbers[i + 1]]);
  return pathBounds(points, true);
}

export class NodeGrid {
  constructor(nodes, cellSize = 50) {
    this.cellSize = cellSize; this.cells = new Map();
    for (const node of nodes) {
      const key = `${Math.floor(node.xMeters / cellSize)},${Math.floor(node.yMeters / cellSize)}`;
      if (!this.cells.has(key)) this.cells.set(key, []);
      this.cells.get(key).push(node.id);
    }
  }
  near([x, y], radius) {
    const ids = [], size = this.cellSize;
    const firstColumn = Math.floor((x - radius) / size), firstRow = Math.floor((y - radius) / size);
    const columns = Math.min(256, Math.floor((x + radius) / size) - firstColumn + 1);
    const rows = Math.min(256, Math.floor((y + radius) / size) - firstRow + 1);
    for (let i = 0; i < columns; i++) {
      for (let j = 0; j < rows; j++) {
        const cell = this.cells.get(`${firstColumn + i},${firstRow + j}`); if (cell) ids.push(...cell);
      }
    }
    return ids;
  }
}

export function canvasPixelRatio(width, height, deviceRatio = 1) {
  return Math.min(Math.max(deviceRatio, 1), 2, Math.sqrt(4_000_000 / Math.max(width * height, 1)));
}
