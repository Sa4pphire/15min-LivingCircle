"""Partition the current isochrone by missing service coverage.

All boolean operations use metres and preserve holes. A missing or truncated
POI query is an unknown category, never evidence of an absent facility.
"""

import math

from shapely import make_valid
from shapely.geometry import GeometryCollection, Point, mapping, shape
from shapely.ops import unary_union

from .sampled_geometry import METERS_PER_DEGREE, local_point_to_bd09


def local_geometry_to_bd09(geometry, origin):
    geometry_type = geometry.get('type')
    if geometry_type not in {'Polygon', 'MultiPolygon'}:
        raise ValueError('盲区几何必须是 Polygon 或 MultiPolygon')

    def convert(value):
        if (isinstance(value, (list, tuple)) and len(value) == 2 and
                all(isinstance(item, (int, float)) for item in value)):
            return local_point_to_bd09(value, origin)
        if not isinstance(value, (list, tuple)):
            raise ValueError('盲区几何坐标无效')
        return [convert(item) for item in value]

    return {'type': geometry_type, 'coordinates': convert(geometry.get('coordinates'))}


def _polygon_parts(geometry):
    if geometry.geom_type == 'Polygon':
        return [geometry] if not geometry.is_empty and geometry.area > 0 else []
    if geometry.geom_type in {'MultiPolygon', 'GeometryCollection'}:
        return [part for item in geometry.geoms for part in _polygon_parts(item)]
    return []


def _polygonal(geometry):
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    parts = _polygon_parts(geometry)
    return unary_union(parts) if parts else GeometryCollection()


def build_blind_zone_coverage(geometry, facilities, origin, *, category_ids,
                            service_radius_meters=1000.0, inventory_complete=True,
                            category_completeness=None, min_zone_area_m2=100.0):
    """Split existing regions once per category instead of enumerating subsets.

    Service circles are a straight-line estimate, not walking isochrones. The
    domain is exclusively the supplied isochrone, including its inner holes.
    Nearby POIs outside that domain may still cover points inside it.
    """
    if geometry.get('type') not in {'Polygon', 'MultiPolygon'}:
        raise ValueError('盲区覆盖面需要 Polygon 或 MultiPolygon 等时圈')
    categories = tuple(dict.fromkeys(category_ids))
    properties = {'status': 'confirmed' if inventory_complete else 'provisional',
                  'categoryIds': list(categories), 'serviceRadiusMeters': service_radius_meters,
                  'geometryMethod': 'incremental-service-partition', 'candidateOnly': True,
                  'coverageMetric': 'straight_line_radius', 'scope': 'isochrone',
                  'minZoneAreaSquareMeters': min_zone_area_m2}
    if not categories or not math.isfinite(service_radius_meters) or service_radius_meters <= 0:
        return {'type': 'FeatureCollection', 'features': [], 'properties': {**properties, 'status': 'empty'}}
    if not math.isfinite(min_zone_area_m2) or min_zone_area_m2 < 0:
        raise ValueError('盲区最小面积必须是有限的非负数')
    assessed = tuple(category for category in categories
                     if (category_completeness.get(category) is True if category_completeness is not None
                         else inventory_complete))
    unknown = [category for category in categories if category not in assessed]
    properties.update(assessedCategoryIds=list(assessed), unknownCategoryIds=unknown)
    if unknown or not inventory_complete:
        properties.update(status='provisional', reason='Only completed category queries can establish missing coverage')
    base = _polygonal(shape(geometry))
    properties['analysisAreaSquareMeters'] = base.area
    if base.is_empty:
        return {'type': 'FeatureCollection', 'features': [], 'properties': {**properties, 'status': 'empty'}}
    if not assessed:
        return {'type': 'FeatureCollection', 'features': [], 'properties': {
            **properties, 'status': 'unknown', 'blindAreaSquareMeters': 0,
            'reason': 'No category has a complete POI query; missing coverage cannot be assessed'}}
    poi_points = {key: set() for key in assessed}
    west, south, east, north = base.bounds
    scale_x = METERS_PER_DEGREE * math.cos(math.radians(float(origin[1])))
    for feature in facilities:
        data = feature.get('properties', {})
        memberships = data.get('categories') or [data.get('category')]
        if not isinstance(memberships, (list, tuple)):
            continue
        try:
            lng, lat = feature['geometry']['coordinates']
            if feature['geometry'].get('type') != 'Point':
                continue
            point = ((float(lng) - origin[0]) * scale_x,
                     (float(lat) - origin[1]) * METERS_PER_DEGREE)
        except (KeyError, TypeError, ValueError):
            continue
        if (all(math.isfinite(value) for value in point)
                and west - service_radius_meters <= point[0] <= east + service_radius_meters
                and south - service_radius_meters <= point[1] <= north + service_radius_meters
                and base.distance(Point(point)) <= service_radius_meters):
            for category in assessed:
                if category in memberships:
                    # Repeated keyword hits and coincident POIs need one circle.
                    poi_points[category].add((round(point[0], 6), round(point[1], 6)))
    regions = {(): base}
    properties['coveragePointCounts'] = {category: len(poi_points[category]) for category in assessed}
    for category in assessed:
        circles = [Point(point).buffer(service_radius_meters, quad_segs=32)
                   for point in sorted(poi_points[category])]
        coverage = _polygonal(unary_union(circles).intersection(base)) if circles else GeometryCollection()
        next_regions = {}
        for missing, area in regions.items():
            if coverage.is_empty or not area.intersects(coverage):
                next_regions[missing + (category,)] = area
            elif coverage.covers(area):
                next_regions[missing] = area
            else:
                covered = _polygonal(area.intersection(coverage))
                uncovered = _polygonal(area.difference(coverage))
                if not covered.is_empty:
                    next_regions[missing] = covered
                if not uncovered.is_empty:
                    next_regions[missing + (category,)] = uncovered
        regions = next_regions
    features = []
    suppressed_area = 0.0
    for missing, area in sorted(regions.items(), key=lambda item: (len(item[0]), item[0])):
        if not missing:
            continue
        # Filtering whole connected parts preserves holes and shared borders;
        # buffering/smoothing individual classes would invent overlapping areas.
        parts = _polygon_parts(area.intersection(base))
        kept = [part for part in parts if part.area >= min_zone_area_m2]
        suppressed_area += sum(part.area for part in parts if part.area < min_zone_area_m2)
        if not kept:
            continue
        zone = unary_union(kept)
        features.append({'type': 'Feature', 'geometry': mapping(zone),
                         'properties': {'missingCategories': list(missing), 'missingCount': len(missing),
                                        'unknownCategories': unknown,
                                        'areaSquareMeters': zone.area,
                                        'approximate': True, 'serviceRadiusMeters': service_radius_meters,
                                        'geometryMethod': properties['geometryMethod']}})
    properties.update(blindAreaSquareMeters=sum(f['properties']['areaSquareMeters'] for f in features),
                      suppressedAreaSquareMeters=suppressed_area)
    return {'type': 'FeatureCollection', 'features': features, 'properties': properties}
