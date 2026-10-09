let activeRegion;

export function getActiveRegion() {
  if (!activeRegion) throw new Error("REGION_NOT_LOADED");
  return activeRegion;
}

export function validateRegion(region) {
  const bounds = region?.boundsMeters;
  const geographicCoordType = region?.geographicCoordType || 'wgs84ll';
  const geographicOrigin = geographicCoordType === 'bd09ll' ? region?.originBd09 : region?.originWgs84;
  if (!region?.id || !['wgs84ll','bd09ll'].includes(geographicCoordType) || !Array.isArray(geographicOrigin) || geographicOrigin.length !== 2 ||
      !geographicOrigin.every(Number.isFinite) || !bounds ||
      ![bounds.minX, bounds.maxX, bounds.minY, bounds.maxY].every(Number.isFinite) ||
      bounds.minX >= bounds.maxX || bounds.minY >= bounds.maxY ||
      region.engineAxis !== "east-north" || region.displayAxis !== "east-south") {
    throw new Error("区域坐标或路网范围无效");
  }
  return { ...region, geographicCoordType, geographicOrigin, displayBounds: { minX: bounds.minX, maxX: bounds.maxX,
    minY: -bounds.maxY, maxY: -bounds.minY } };
}

async function readRegion(endpoint, fetchImpl, { signal } = {}) {
  async function read(url) {
    const response = await fetchImpl(url, { cache: "no-cache", signal });
    const body = await response.json();
    if (!response.ok) throw new Error(body?.detail?.message ?? `区域加载失败 HTTP ${response.status}`);
    return body;
  }
  const metadata = validateRegion(await read(endpoint));
  const [context, alignment] = await Promise.all([
    read(metadata.assets.context),
    metadata.assets.alignment ? read(metadata.assets.alignment) : null,
  ]);
  const originKey = metadata.geographicCoordType === 'bd09ll' ? 'originBd09' : 'originWgs84';
  if (JSON.stringify(context[originKey]) !== JSON.stringify(metadata.geographicOrigin)) {
    throw new Error("区域底图与路网原点不一致");
  }
  return Object.freeze({ ...metadata, context, alignment });
}

export function activateRegion(region) {
  validateRegion(region);
  if (!region.context) throw new Error('区域底图尚未加载');
  activeRegion = region;
  return region;
}

export async function readDisplayRegion(identity, fetchImpl = fetch, options = {}) {
  const region = await readRegion(`/api/v1/regions/${encodeURIComponent(identity)}`, fetchImpl, options);
  if (region.id !== identity) throw new Error('区域包标识与选择不一致');
  return region;
}

export async function loadActiveRegion(fetchImpl = fetch, editorRegionId = null) {
  const endpoint = editorRegionId ? `/api/v1/network-editor/regions/${encodeURIComponent(editorRegionId)}` : '/api/v1/region';
  return activateRegion(await readRegion(endpoint, fetchImpl));
}
