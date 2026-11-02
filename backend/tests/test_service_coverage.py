import unittest
from unittest.mock import AsyncMock, patch

from shapely.geometry import Point, Polygon, mapping, shape

from app.blind_zone_coverage import build_blind_zone_coverage, local_geometry_to_bd09
from app.service_coverage import collect_service_coverage, SERVICE_CATEGORIES


class CoverageTests(unittest.IsolatedAsyncioTestCase):
    origin = (121.5, 31.3)

    def report(self):
        area = Polygon([(-1400, -1400), (1400, -1400), (1400, 1400), (-1400, 1400)],
                       [[(-100, -100), (-100, 100), (100, 100), (100, -100)]])
        return {'coordinateSystem': 'bd09ll', 'isochrone': local_geometry_to_bd09(mapping(area), self.origin)}

    async def query(self, *, complete=False, records=None, stale=0):
        info = {'categories': [{'category': key, 'paginationComplete': complete, 'error': None}
                               for key in SERVICE_CATEGORIES], 'stalePages': stale}
        with patch('app.service_coverage.search_tiles', AsyncMock(return_value=(records or [], info))) as search:
            result = await collect_service_coverage(object(), self.report())
            self.assertTrue(search.call_args.kwargs['cache_only'])
            self.assertEqual(set(search.call_args.args[1]), {'market', 'pharmacy', 'primary_school'})
            bounds = search.call_args.kwargs['bounds']
            self.assertLess(bounds[0], shape(self.report()['isochrone']).bounds[0])
            return result

    async def test_no_observations_never_invents_red_estimate(self):
        result = await self.query()
        self.assertFalse(result['complete'])
        self.assertFalse(result['canEstimate'])
        self.assertEqual(result['estimated']['features'], [])

    async def test_incomplete_inventory_is_only_an_estimate(self):
        record = {'uid': 'pharmacy1', 'lng': self.origin[0], 'lat': self.origin[1],
                  'categories': ['pharmacy'], 'category': 'pharmacy'}
        result = await self.query(records=[record])
        self.assertTrue(result['canEstimate'])
        self.assertEqual(result['assessed']['features'], [])
        self.assertTrue(result['estimated']['features'])
        domain = shape(result['domain'])
        for feature in result['estimated']['features']:
            self.assertEqual(feature['properties']['confidence'], 'estimate')
            self.assertLess(shape(feature['geometry']).difference(domain).area, 1e-14)
            self.assertFalse(shape(feature['geometry']).contains(Point(self.origin)))

    async def test_complete_empty_queries_can_report_three_missing(self):
        result = await self.query(complete=True)
        self.assertTrue(result['complete'])
        self.assertEqual(result['assessed']['features'][0]['properties']['missingCount'], 3)
        self.assertIsNone(result['positionalAccuracyMeters'])
        self.assertFalse(result['inventoryVerified'])

    async def test_stale_queries_cannot_claim_completeness(self):
        self.assertFalse((await self.query(complete=True, stale=1))['complete'])

    def test_facility_outside_domain_still_covers_domain(self):
        area = mapping(Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]))
        point = local_geometry_to_bd09({'type': 'Polygon', 'coordinates': [[[200, 50]]]}, self.origin)['coordinates'][0][0]
        facility = {'geometry': {'type': 'Point', 'coordinates': point}, 'properties': {'category': 'pharmacy'}}
        result = build_blind_zone_coverage(area, [facility], self.origin, category_ids=['pharmacy'])
        self.assertEqual(result['features'], [])


if __name__ == '__main__':
    unittest.main()
