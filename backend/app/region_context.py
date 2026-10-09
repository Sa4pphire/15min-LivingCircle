"""Generate portable display contours from OSM, in the selected package's frame."""
from collections import Counter
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

import httpx

from .baidu.client import BaiduClient
from .baidu.errors import BaiduApiError
from .map_assets import valid_point
from .region_export import clip_path

OVERPASS_URLS = ('https://overpass-api.de/api/interpreter',
                 'https://overpass.kumi.systems/api/interpreter')
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024


def selection_bounds(bounds):
    if (set(bounds) != {'minX', 'maxX', 'minY', 'maxY'} or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds.values())):
        raise ValueError('轮廓框选范围无效')
    width, height = bounds['maxX']-bounds['minX'], bounds['maxY']-bounds['minY']
    if min(width, height) < 20:
        raise ValueError('轮廓选框的宽和高至少需要 20 米')
    if max(width, height) > 6000 or width*height > 25_000_000:
        raise ValueError('请缩小选框：单边最多 6 公里，面积最多 25 平方公里')
    return dict(bounds)


def query_frame(region, bounds):
    graph = region.graph()
    key = 'originWgs84' if 'originWgs84' in graph else 'originBd09'
    origin = graph[key]
    scale_x = 111320 * math.cos(math.radians(origin['lat']))
    # The native BD-09 box is only a coarse search envelope. Extra padding retrieves
    # nearby WGS-84 geometries; exact display clipping follows OFFICIAL forward conversion.
    padding = 1500 if key == 'originBd09' else 20
    west = origin['lng'] + (bounds['minX']-padding)/scale_x
    east = origin['lng'] + (bounds['maxX']+padding)/scale_x
    south = origin['lat'] + (bounds['minY']-padding)/111320
    north = origin['lat'] + (bounds['maxY']+padding)/111320
    if not (-180 <= west < east <= 180 and -85 < south < north < 85):
        raise ValueError('选区超出支持的地理范围')
    box = {'minX': west, 'maxX': east, 'minY': south, 'maxY': north}
    return key, origin, scale_x, box


def osm_query(box):
    bbox = '(' + ','.join(f'{box[k]:.7f}' for k in ('minY','minX','maxY','maxX')) + ')'
    return ('[out:json][timeout:60];('
            f'way[highway]{bbox};way[waterway]{bbox};'
            f'wr[building]{bbox};wr[natural=water]{bbox};wr[leisure=park]{bbox};'
            f'wr[landuse~"^(grass|forest|recreation_ground)$"]{bbox};);out tags geom;')


async def fetch_osm(query, cache_root: Path):
    cache_root.mkdir(parents=True, exist_ok=True)
    path = cache_root / (hashlib.sha256(query.encode()).hexdigest()+'.json')
    if path.is_file() and datetime.now().timestamp()-path.stat().st_mtime < 86400:
        return json.loads(path.read_text('utf-8'))
    async with httpx.AsyncClient(timeout=httpx.Timeout(75, connect=12),
                                 headers={'User-Agent': '15min-LivingCircle local contour builder'}) as client:
        for url in OVERPASS_URLS:
            try:
                async with client.stream('POST', url, data={'data': query}) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise ValueError('轮廓数据过大，请缩小框选范围')
                        chunks.append(chunk)
                data = json.loads(b''.join(chunks))
                if not isinstance(data, dict) or not isinstance(data.get('elements'), list) or data.get('remark'):
                    raise ValueError('OSM 未返回完整几何，请缩小选框后重试')
                temporary = path.with_suffix('.'+uuid4().hex+'.tmp')
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                temporary.replace(path)
                return data
            except (httpx.HTTPError, json.JSONDecodeError):
                continue
    raise ValueError('OSM 轮廓下载失败，请检查网络，稍后重试')


def classify(tags):
    if tags.get('natural') == 'water': return 'waterArea'
    if tags.get('waterway'): return 'waterLine'
    if tags.get('leisure') == 'park' or tags.get('landuse') in ('grass','forest','recreation_ground'): return 'park'
    if tags.get('building'): return 'building'
    highway = tags.get('highway')
    if highway in ('trunk','primary','secondary','tertiary','trunk_link','primary_link','secondary_link','tertiary_link'): return 'roadMajor'
    if highway in ('residential','unclassified','pedestrian','service','living_street'): return 'roadLocal'
    if highway in ('footway','path','cycleway','steps'): return 'roadPath'
    return None


def geometry(points):
    result = []
    for point in points or []:
        coordinate = [point.get('lon'), point.get('lat')] if isinstance(point, dict) else None
        if not valid_point(coordinate): return []
        if not result or result[-1] != coordinate: result.append(coordinate)
    return result


def stitch(parts):
    pending = [list(part) for part in parts if len(part) >= 2]
    rings = []
    while pending:
        ring = pending.pop()
        while ring[0] != ring[-1]:
            for i, part in enumerate(pending):
                if ring[-1] == part[0]: ring.extend(part[1:])
                elif ring[-1] == part[-1]: ring.extend(part[-2::-1])
                elif ring[0] == part[-1]: ring = part[:-1]+ring
                elif ring[0] == part[0]: ring = part[:0:-1]+ring
                else: continue
                pending.pop(i)
                break
            else: break
        if len(ring) >= 4 and ring[0] == ring[-1]: rings.append(ring)
    return rings


def contains(point, ring):
    inside = False
    x, y = point
    for a, b in zip(ring, ring[1:]):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def clip_ring(ring, bounds):
    points = ring[:-1] if ring and ring[0] == ring[-1] else ring
    for axis, value, sign in ((0,bounds['minX'],1),(0,bounds['maxX'],-1),
                               (1,bounds['minY'],1),(1,bounds['maxY'],-1)):
        output = []
        if not points: return []
        for start, end in zip(points[-1:]+points[:-1], points):
            a, b = sign*(start[axis]-value) >= 0, sign*(end[axis]-value) >= 0
            if a != b:
                ratio = (value-start[axis])/(end[axis]-start[axis])
                crossing = [start[i]+ratio*(end[i]-start[i]) for i in (0,1)]
                crossing[axis] = value
                output.append(crossing)
            if b: output.append(end)
        points = output
    unique = []
    for p in points:
        if not unique or math.dist(p, unique[-1]) > 1e-8: unique.append(p)
    if len(unique) < 3: return []
    if unique[0] != unique[-1]: unique.append(unique[0])
    return unique


def simplify(points, tolerance=1):
    if len(points) <= 2: return points
    keep, pending = {0,len(points)-1}, [(0,len(points)-1)]
    while pending:
        left, right = pending.pop()
        a, b = points[left], points[right]
        dx, dy = b[0]-a[0], b[1]-a[1]
        denominator = dx*dx+dy*dy
        farthest, index = tolerance, None
        for i in range(left+1,right):
            p = points[i]
            t = max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/denominator)) if denominator else 0
            distance = math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)
            if distance > farthest: farthest, index = distance, i
        if index is not None:
            keep.add(index); pending.extend(((left,index),(index,right)))
    return [points[i] for i in sorted(keep)]


def svg_path(points, closed=False):
    simplified = simplify(points)
    if closed and len(simplified) < 4: simplified = points
    return ''.join(f'{"M" if i==0 else "L"}{p[0]:.1f} {p[1]:.1f}' for i,p in enumerate(simplified)) + ('Z' if closed else '')


async def projector(key, origin, scale_x, box, client):
    if key == 'originWgs84':
        return lambda p: [(p[0]-origin['lng'])*scale_x,(origin['lat']-p[1])*111320]
    step_x, step_y = 250/scale_x, 250/111320
    columns = math.ceil((box['maxX']-box['minX'])/step_x)+1
    rows = math.ceil((box['maxY']-box['minY'])/step_y)+1
    anchors = [(round(box['minX']+col*step_x,10),round(box['minY']+row*step_y,10))
               for row in range(rows) for col in range(columns)]
    if len(anchors) > 2500: raise ValueError('坐标校准范围过大，请缩小选框')
    converted = []
    for offset in range(0,len(anchors),100):
        batch = anchors[offset:offset+100]
        result = await client.convert_coordinates(batch,'wgs84ll')
        if len(result) != len(batch) or not all(valid_point(p) for p in result):
            raise BaiduApiError('轮廓坐标转换结果无效')
        converted.extend(result)
    for row in range(rows-1):
        for col in range(columns-1):
            a,b = converted[row*columns+col:row*columns+col+2]
            c,d = converted[(row+1)*columns+col:(row+1)*columns+col+2]
            for du in ((b[0]-a[0],b[1]-a[1]),(d[0]-c[0],d[1]-c[1])):
                for dv in ((c[0]-a[0],c[1]-a[1]),(d[0]-b[0],d[1]-b[1])):
                    if du[0]*dv[1]-du[1]*dv[0] <= 1e-15:
                        raise BaiduApiError('轮廓坐标校准网格退化，请重新生成')
    def project(point):
        x = max(0,min(columns-1,(point[0]-box['minX'])/step_x))
        y = max(0,min(rows-1,(point[1]-box['minY'])/step_y))
        col, row = min(columns-2,int(x)), min(rows-2,int(y))
        u, v = x-col, y-row
        a,b = converted[row*columns+col:row*columns+col+2]
        c,d = converted[(row+1)*columns+col:(row+1)*columns+col+2]
        bd = [(1-v)*((1-u)*a[i]+u*b[i])+v*((1-u)*c[i]+u*d[i]) for i in (0,1)]
        return [(bd[0]-origin['lng'])*scale_x,(origin['lat']-bd[1])*111320]
    return project


def build_features(data, box, project, display):
    features, used_members, skipped = [], set(), 0
    elements = data.get('elements', [])
    if len(elements) > 100000: raise ValueError('OSM 要素过多，请缩小选框')
    for element in sorted(elements, key=lambda e: e.get('type') != 'relation'):
        tags = element.get('tags', {})
        kind = classify(tags) if isinstance(tags, dict) else None
        if not kind or (element.get('type') == 'way' and element.get('id') in used_members): continue
        polygon = kind in ('building','waterArea','park')
        if element.get('type') == 'relation':
            members = [m for m in element.get('members',[]) if m.get('type') == 'way']
            outer = stitch([geometry(m.get('geometry')) for m in members if m.get('role','outer') in ('','outer')])
            inner = stitch([geometry(m.get('geometry')) for m in members if m.get('role') == 'inner'])
            rings = outer + [r for r in inner if any(contains(r[0],o) for o in outer)]
            if not polygon or not outer: skipped += 1; continue
            used_members.update(m.get('ref') for m in members)
        else:
            path = geometry(element.get('geometry'))
            if len(path) < 2 or (polygon and (len(path)<4 or path[0] != path[-1])):
                skipped += 1; continue
            rings = [path]
        paths = []
        for ring in rings:
            # Clip remote way tails to the geographic envelope before interpolation.
            geographic = [clip_ring(ring,box)] if polygon else [r['path'] for r in clip_path(ring,box,minimum_length=1e-10)[0]]
            for part in geographic:
                if not part: continue
                local = [project(p) for p in part]
                clipped = [clip_ring(local,display)] if polygon else [r['path'] for r in clip_path(local,display)[0]]
                paths.extend(svg_path(p,polygon) for p in clipped if len(p) >= (4 if polygon else 2))
        if paths:
            features.append({'id': f"osm:{element['type']}:{element['id']}", 'kind': kind,
                             'd': ''.join(paths), **({'fillRule':'evenodd'} if polygon else {})})
    if len(features) > 20000: raise ValueError('轮廓要素过多，请缩小选框')
    return features, skipped


async def generate_context(region, bounds, cache_root, *, client=None):
    bounds = selection_bounds(bounds)
    key, origin, scale_x, box = query_frame(region,bounds)
    query = osm_query(box)
    data = await fetch_osm(query,cache_root/'osm-context')
    owned = client is None
    client = client or BaiduClient()
    try:
        project = await projector(key,origin,scale_x,box,client)
        display = {**bounds,'minY':-bounds['maxY'],'maxY':-bounds['minY']}
        features, skipped = await asyncio.to_thread(build_features,data,box,project,display)
    finally:
        if owned: await client.aclose()
    if not features: raise ValueError('选区内没有可用的 OSM 轮廓，请扩大选区或更换街区')
    context = {'schemaVersion':1, key:[origin['lng'],origin['lat']], 'features':features,
        'coordType': 'local-meters-from-'+('bd09' if key=='originBd09' else 'wgs84'),
        'source':'OpenStreetMap contributors','sourceLicense':'ODbL 1.0',
        'sourceUrl':'https://www.openstreetmap.org/copyright', 'sourceQuery':query,
        'generatedAt':datetime.now(timezone.utc).isoformat(), 'boundsMeters':bounds,
        'conversion':{'model':'official_baidu_250m_grid_to_native_local' if key=='originBd09' else 'wgs84_to_local',
                      'queryEnvelopeApproximate':key=='originBd09','simplificationMeters':1}}
    return {'context':context,'boundsMeters':bounds,'featureCount':len(features),
            'counts':dict(Counter(f['kind'] for f in features)),'skippedElementCount':skipped}
