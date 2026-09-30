import asyncio

from app.representative_routes import (
    collect_representative_routes,
    select_representative_pois,
)
from app.baidu.errors import BaiduApiError


def test_selects_nearest_poi_once_per_category() -> None:
    facilities = [
        {"uid": "school-far", "category": "education", "lng": 121.53, "lat": 31.34},
        {"uid": "school-near", "category": "education", "lng": 121.514, "lat": 31.337},
        {"uid": "shop", "category": "shopping", "lng": 121.512, "lat": 31.337},
    ]
    selected = select_representative_pois(facilities, (121.513, 31.337))
    assert [item["uid"] for item in selected] == ["school-near", "shop"]


def test_route_failure_is_partial_and_keeps_other_routes() -> None:
    class FakeClient:
        async def walking_route(self, origin, destination, *, destination_uid=None):
            if destination_uid == "bad":
                raise BaiduApiError("route unavailable")
            return {
                "distanceMeters": 300.0,
                "durationSeconds": 240.0,
                "segments": [[[121.513, 31.337], [121.514, 31.337]]],
            }

    facilities = [
        {"uid": "bad", "category": "education", "lng": 121.514, "lat": 31.337},
        {"uid": "good", "category": "shopping", "lng": 121.515, "lat": 31.337},
    ]
    routes, failures = asyncio.run(collect_representative_routes(
        FakeClient(), (121.513, 31.337), facilities,
    ))
    assert [route["poiUid"] for route in routes] == ["good"]
    assert failures == [{"uid": "bad", "error": "route unavailable"}]
