import { poiInfo, visiblePois } from './poiFacilities.js';

export const lifeCategories = [
  { id: 'education', label: '学校', glyph: '校', color: '#2f6e91' },
  { id: 'healthcare', label: '医院', glyph: '医', color: '#a53e50' },
  { id: 'shopping', label: '商店', glyph: '店', color: '#795d18' },
  { id: 'public_service', label: '便民服务', glyph: '公', color: '#68509b' },
  { id: 'dining', label: '餐饮', glyph: '餐', color: '#b45f2f' },
];

const finitePoint = point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite);

export function resultMeterGeometry(result) {
  const geometry = result?.coordinateSystem === 'preview-local-v1'
    ? result.displayArea?.geometry : result?.fallbackDisplayArea?.geometry;
  return ['Polygon', 'MultiPolygon'].includes(geometry?.type) ? geometry : null;
}

export function geometryAreaMeters(geometry) {
  const polygons = geometry?.type === 'MultiPolygon' ? geometry.coordinates
    : geometry?.type === 'Polygon' ? [geometry.coordinates] : [];
  function ringArea(ring) {
    if (!Array.isArray(ring) || ring.length < 3 || !ring.every(finitePoint)) return null;
    // Translate first to retain precision for large, offset local coordinates.
    const [ox, oy] = ring[0];
    let sum = 0;
    for (let i = 0; i < ring.length; i += 1) {
      const a = ring[i], b = ring[(i + 1) % ring.length];
      sum += (a[0] - ox) * (b[1] - oy) - (b[0] - ox) * (a[1] - oy);
    }
    return Math.abs(sum) / 2;
  }
  let total = 0;
  for (const polygon of polygons ?? []) {
    if (!Array.isArray(polygon) || !polygon.length) return null;
    const rings = polygon.map(ringArea);
    if (rings.some(area => area === null)) return null;
    total += Math.max(0, rings[0] - rings.slice(1).reduce((sum, area) => sum + area, 0));
  }
  return total > 0 ? total : null;
}

export function nearbyPlaces(result, category = 'all', query = '') {
  const entries = [...new Map(visiblePois(result).filter(poi => typeof poi.id === 'string' && poi.id)
    .map(poi => [poi.id, poi])).values()];
  const term = query.trim().toLocaleLowerCase();
  return entries.filter(poi => (category === 'all' || (poi.categories ?? [poi.category]).includes(category))
    && (!term || `${poi.name ?? ''} ${poi.address ?? ''}`.toLocaleLowerCase().includes(term)));
}

export function placeWalkingSeconds(result, poi, selectedRoute = null) {
  if (selectedRoute?.poiId === poi.id && selectedRoute.status === 'ready'
    && Number.isFinite(selectedRoute.travelTimeSeconds) && selectedRoute.travelTimeSeconds >= 0) {
    return selectedRoute.travelTimeSeconds;
  }
  if (poi.modelReachable === true && Number.isFinite(poi.modelTravelTimeSeconds)
    && poi.modelTravelTimeSeconds >= 0) return poi.modelTravelTimeSeconds;
  const segment = result?.routeSegments?.find(route => route.poiUid === poi.id
    && Number.isFinite(route.durationSeconds) && route.durationSeconds >= 0);
  return segment?.durationSeconds ?? null;
}

export function simplePlaceStatus(result, state = 'idle') {
  const info = poiInfo(result);
  if (state === 'enriching' || info?.status === 'pending') return '范围已生成，正在补充附近地点。';
  if (info?.status === 'unavailable') return '地点信息暂时没能加载，地图范围仍可查看。';
  if (info?.status === 'partial' || info?.stalePages || info?.refreshRequired) return '地点信息还不齐，先看看已找到的。';
  return '这里列出地图范围内的地点，点开可查看路线。';
}

export function serviceReminder(result) {
  if (!['confirmed', 'provisional'].includes(result?.blindZoneStatus)) return null;
  const missing = new Set((result.blindZones?.features ?? []).flatMap(feature => feature.properties?.missingCategories ?? []));
  const names = lifeCategories.filter(category => missing.has(category.id)).map(category => category.label);
  if (!names.length) return null;
  return `范围里有些地方，周边 1 公里内暂未找到${names.join('、')}。`;
}
