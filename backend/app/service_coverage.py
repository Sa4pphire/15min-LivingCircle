"""Three-service coverage, independent of the five-category browsing inventory.

All output is BD-09. Distances are straight-line metres, not walking times.
An incomplete query can only produce an explicitly requested estimate.
"""
import json
import math

from shapely.geometry import mapping, shape
from shapely.ops import transform

from .blind_zone_coverage import build_blind_zone_coverage, local_geometry_to_bd09
from .local_alignment import load_grid_frame
from .poi import POI_CATEGORIES
from .poi_search import search_tiles
from .region_package import load_region_by_id
from .sampled_geometry import METERS_PER_DEGREE
from .schemas import CenterPoint
from .settings import settings

SERVICE_CATEGORIES = {key: {'label': label, 'keywords': (label,)}
                      for key, label in POI_CATEGORIES.items()}


def analysis_geometry_bd09(report):
    raw = report['isochrone']
    geometry = raw.get('geometry', raw)
    coord = raw.get('properties', {}).get('coordType', report.get('coordinateSystem'))
    if coord == 'bd09ll':
        return geometry
    if coord != 'wgs84ll':
        raise ValueError('当前结果的坐标系不可用于服务覆盖分析')
    region = load_region_by_id(report.get('metadata', {}).get('regionId') or settings.region_id)
    origin = region.graph()['originWgs84']
    path = region.file('alignment')
    if load_grid_frame({'coordType': 'wgs84ll', 'originWgs84': origin}, path) is None:
        raise ValueError('当前区域缺少经过校验的地图坐标校准数据')
    grid = json.loads(path.read_text(encoding='utf-8'))['alignment']
    columns, rows = grid['columns'], grid['rows']
    scale = METERS_PER_DEGREE * math.cos(math.radians(origin['lat']))

    def convert(lng, lat, z=None):
        # Same east-south bilinear mesh as the frontend; never relabel WGS as BD.
        u = ((lng - origin['lng']) * scale - grid['minLocalMeters'][0]) / grid['stepMeters']
        v = (-(lat - origin['lat']) * METERS_PER_DEGREE - grid['minLocalMeters'][1]) / grid['stepMeters']
        if not (-1e-7 <= u <= columns-1+1e-7 and -1e-7 <= v <= rows-1+1e-7):
            raise ValueError('等时圈超出当前区域坐标校准范围')
        u, v = max(0, min(columns-1, u)), max(0, min(rows-1, v))
        col, row = min(columns-2, int(u)), min(rows-2, int(v))
        u, v = u-col, v-row
        a, b, c, d = [grid['pointsBd09'][i] for i in
                      (row*columns+col, row*columns+col+1, (row+1)*columns+col, (row+1)*columns+col+1)]
        return tuple((1-v)*((1-u)*a[i]+u*b[i])+v*((1-u)*c[i]+u*d[i]) for i in (0, 1))

    return mapping(transform(convert, shape(geometry)))


async def collect_service_coverage(client, report, *, online=False):
    geometry = analysis_geometry_bd09(report)
    area = shape(geometry)
    if area.is_empty or area.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError('请先生成有效的等时圈')
    west, south, east, north = area.bounds
    origin = ((west+east)/2, (south+north)/2)
    scale = METERS_PER_DEGREE * math.cos(math.radians(origin[1]))
    local = mapping(transform(lambda x, y, z=None: ((x-origin[0])*scale,
                            (y-origin[1])*METERS_PER_DEGREE), area))
    bounds = [west-1000/scale, south-1000/METERS_PER_DEGREE,
              east+1000/scale, north+1000/METERS_PER_DEGREE]
    # Include facilities outside the isochrone that cover its edge. This is a
    # coverage inventory query; it does not expand the user's walking routes.
    records, info = await search_tiles(client, SERVICE_CATEGORIES,
        CenterPoint(lng=origin[0], lat=origin[1], coordType='bd09ll'), 1000,
        settings, bounds=bounds, cache_only=not online)
    facilities = [{'type': 'Feature', 'geometry': {'type': 'Point',
                   'coordinates': [item['lng'], item['lat']]}, 'properties': item} for item in records]
    complete = {item['category']: bool(item['paginationComplete'] and not item['error'] and not info.get('stalePages'))
                for item in info['categories']}
    known = all(complete.get(key, False) for key in SERVICE_CATEGORIES)

    def calculate(estimate):
        layer = build_blind_zone_coverage(local, facilities, origin,
            category_ids=SERVICE_CATEGORIES, inventory_complete=known,
            category_completeness={key: True for key in SERVICE_CATEGORIES} if estimate else complete)
        for feature in layer['features']:
            feature['geometry'] = local_geometry_to_bd09(feature['geometry'], origin)
            feature['properties']['confidence'] = 'estimate' if estimate else 'query_complete'
        return layer

    assessed = calculate(False)
    # No observations + incomplete retrieval is unknown, never all-services absent.
    estimated = calculate(True) if records and not known else assessed
    return {'coordinateSystem': 'bd09ll', 'domain': geometry,
            'assessed': assessed, 'estimated': estimated, 'complete': known,
            'canEstimate': bool(records) and not known,
            'categoryLabels': POI_CATEGORIES, 'categoryCompleteness': complete,
            'coverageMetric': 'straight_line_radius', 'serviceRadiusMeters': 1000,
            'inventoryVerified': False, 'positionalAccuracyMeters': None,
            'categoryCounts': {key: sum(key in item['categories'] for item in records)
                               for key in SERVICE_CATEGORIES},
            'metadata': info}
