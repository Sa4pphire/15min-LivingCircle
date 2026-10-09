import BMapLoader from '@baidumap/jsapi-loader';
import { createLocalBd09Alignment } from './mapAsset.js';
import { coverageCorrection } from './mapCoverage.js';

// Keep one native map per editor page. Destroying it during a region switch leaves
// pending SDK JSONP callbacks reading deleted configuration (getLanguage).
let reusableMap = null;

export async function locateEditorRegion(location, ak) {
  if (!ak) throw new Error('新建地点需要浏览器地图 AK，请先配置 frontend/.env.local 并重启前端。');
  const BMap = await BMapLoader.load({ ak, version: '4.0', timeout: 18000 });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('地点查询超时，请检查网络后重试。')), 12000);
    new BMap.Geocoder().getPoint(location, point => {
      clearTimeout(timer);
      if (!point || !Number.isFinite(point.lng) || !Number.isFinite(point.lat)) {
        reject(new Error('未能定位这个地点，请输入省市、区县或附近地标后重试。')); return;
      }
      resolve([point.lng, point.lat]);
    }, '');
  });
}

// The map and editing canvas use the same official grid in both directions.
export async function createEditorBaiduMap(element, region, ak, onChange) {
  const alignment = createLocalBd09Alignment(region.alignment);
  const BMap = await BMapLoader.load({ ak, version: '4.0', timeout: 18000 });
  const center = [(region.boundsMeters.minX+region.boundsMeters.maxX)/2,
    (region.boundsMeters.minY+region.boundsMeters.maxY)/2];
  const bdCenter = alignment.toBd09([center[0], -center[1]]);
  if (!bdCenter) throw new Error('当前区域缺少有效的百度坐标校准');
  const surface = reusableMap?.surface || document.createElement('div');
  surface.style.cssText = 'position:absolute;inset:0';
  element.append(surface);
  const map = reusableMap?.map || new BMap.Map(surface, { enableMapClick: false });
  const owner = Symbol('editor-map');
  reusableMap = { map, surface, owner };
  let disposed = false, syncing = false, lastWidth = 0, lastHeight = 0;
  map.enableResizeOnCenter?.();
  map.checkResize?.();
  map.enableAutoResize?.();
  map.centerAndZoom(new BMap.Point(...bdCenter), region.authoringWorkspace ? 13 : 16, { noAnimation: true });
  // A reattached WebGL surface needs a layout/render pass before pixel projection
  // reflects its container size; never publish the old detached projection.
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const minZoom = map.getMinZoom?.() ?? 3, maxZoom = map.getMaxZoom?.() ?? 21;
  for (const method of ['disableDragging', 'disableScrollWheelZoom', 'disableDoubleClickZoom',
    'disableKeyboard', 'disablePinchToZoom', 'disableInertialDragging']) map[method]?.();

  function toScreen([x, y]) {
    const coordinate = alignment.toBd09([x, -y]);
    if (!coordinate) return null;
    const point = map.pointToPixel(new BMap.Point(...coordinate));
    return [point.x, point.y];
  }
  function toWorld([x, y]) {
    const point = map.pixelToPoint(new BMap.Pixel(x, y));
    const local = alignment.toLocal([point.lng, point.lat]);
    return local ? [local[0], -local[1]] : null;
  }
  function actualView(width, height) {
    const midpoint = toWorld([width/2, height/2]);
    if (!midpoint) return null;
    const grid = region.alignment.alignment;
    const native = grid.kind === 'bd09_local_meters';
    const span = native ? 100 : Math.min(100, (grid.columns-1)*grid.stepMeters);
    const sampleX = native ? midpoint[0]-50 : Math.max(grid.minLocalMeters[0], Math.min(midpoint[0]-span/2,
      grid.minLocalMeters[0]+(grid.columns-1)*grid.stepMeters-span));
    const west = toScreen([sampleX, midpoint[1]]), east = toScreen([sampleX+span, midpoint[1]]);
    if (!west || !east) return null;
    return { x: midpoint[0], y: midpoint[1], scale: Math.hypot(east[0]-west[0], east[1]-west[1]) / span };
  }
  function changed() { if (!disposed && !syncing) onChange(); }
  const events = ['moveend', 'zoomend', 'tilesloaded'];
  events.forEach(event => map.addEventListener(event, changed));

  return {
    toScreen, toWorld,
    sync(view, width, height, displayBounds) {
      if (disposed) return view;
      syncing = true;
      try {
        if (width !== lastWidth || height !== lastHeight) {
          map.checkResize?.();
          lastWidth = width; lastHeight = height;
        }
        let actual = actualView(width, height);
        // A scale sample can be outside a small cropped grid during initial setup.
        const scale = actual?.scale || 0.85 * 2 ** (map.getZoom()-18);
        const minimum = displayBounds ? Math.max(width/(displayBounds.maxX-displayBounds.minX),
          height/(displayBounds.maxY-displayBounds.minY)) : 0;
        if (minimum > scale * 2 ** (maxZoom-map.getZoom()) * 1.001) {
          throw new Error('区域范围太窄，超出百度地图可用的最大缩放，请使用本地底图');
        }
        let level = map.getZoom() + Math.round(Math.log2(view.scale/scale));
        if (scale * 2 ** (level-map.getZoom()) < minimum) level++;
        level = Math.max(minZoom, Math.min(maxZoom, level));
        const coordinate = alignment.toBd09([view.x, -view.y]);
        if (!coordinate) throw new Error('地图中心超出当前区域的百度校准范围');
        const current = map.getCenter();
        if (level !== map.getZoom() || Math.abs(current.lng-coordinate[0]) > 1e-10 ||
            Math.abs(current.lat-coordinate[1]) > 1e-10) {
          map.centerAndZoom(new BMap.Point(...coordinate), level, { noAnimation: true });
        }
        const points = [[0,0],[width/2,0],[width,0],[0,height/2],[width,height/2],
          [0,height],[width/2,height],[width,height]].map(toWorld);
        if (displayBounds && points.every(Boolean)) {
          const correction = coverageCorrection(points.map(([x,y]) => [x,-y]), displayBounds);
          if (correction.zoomRatio > 1.000001 && level < maxZoom) level++;
          actual = actualView(width, height);
          if (actual && (Math.abs(correction.dx)>0.05 || Math.abs(correction.dy)>0.05 || level !== map.getZoom())) {
            const target = alignment.toBd09([actual.x+correction.dx, -actual.y+correction.dy]);
            if (target) map.centerAndZoom(new BMap.Point(...target), level, { noAnimation: true });
          }
        }
        return actualView(width, height) || view;
      } finally { syncing = false; }
    },
    zoom(factor, point, width, height) {
      if (disposed || !Number.isFinite(factor) || factor <= 0) return null;
      const level = Math.max(minZoom, Math.min(maxZoom, map.getZoom() + Math.round(Math.log2(factor))));
      if (level === map.getZoom()) return actualView(width, height);
      const before = toWorld(point);
      map.setZoom(level, { noAnimation: true });
      const after = toWorld(point), actual = actualView(width, height);
      return before && after && actual ? { ...actual,
        x: actual.x+before[0]-after[0], y: actual.y+before[1]-after[1] } : actual;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      events.forEach(event => map.removeEventListener(event, changed));
      if (reusableMap.owner === owner) {
        map.disableAutoResize?.();
        surface.remove();
      }
    },
  };
}
