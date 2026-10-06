"""Plan, or explicitly fetch, POI cache pages for the fixed demo area.

Default is offline planning. --fetch uses the configured server AK and the
existing bounded, paced cache-first search; no credentials appear in output.
"""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app import pois
from app.network import _to_map_coordinate
from app.poi_search import plan_tiles
from app.schemas import CenterPoint
from app.settings import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Allow bounded online requests.")
    parser.add_argument("--max-requests", type=int, default=16)
    parser.add_argument("--budget-seconds", type=float, default=12)
    args = parser.parse_args()
    if not 1 <= args.max_requests <= 96 or not 0 < args.budget_seconds <= 60:
        parser.error("requests must be 1..96; budget must be >0 and <=60 seconds")
    graph = json.loads(settings.synthetic_network_path.read_text(encoding="utf-8"))
    origin = graph["originWgs84"]
    bounds = graph["supportedCenterBoundsMeters"]
    # 900*1.3 is the maximum straight-line reach, including origin access;
    # this inventory region is not a graph crop or a claimed isochrone.
    margin = 900 * 1.3
    west, south = _to_map_coordinate([bounds["minX"]-margin, bounds["minY"]-margin], origin)
    east, north = _to_map_coordinate([bounds["maxX"]+margin, bounds["maxY"]+margin], origin)
    center = CenterPoint(**origin, coordType="wgs84ll")
    pois.settings = replace(settings, poi_max_requests=args.max_requests,
                            poi_budget_seconds=args.budget_seconds)
    _, plan = plan_tiles(center, 2340, pois.settings, [west, south, east, north])
    print(json.dumps({"fetch": args.fetch, "plan": plan, "maxRequests": args.max_requests,
                      "budgetSeconds": args.budget_seconds}, ensure_ascii=False))
    if not args.fetch:
        return

    async def collect():
        service = pois.PoiService()
        try:
            records, metadata = await service.search(center, 2340, pois.CATEGORIES,
                                                    bounds=[west, south, east, north])
            print(json.dumps({"candidateCount": len(records), "status": metadata["status"],
                              "apiRequests": metadata["apiRequests"], "cacheHits": metadata["cacheHits"],
                              "completedQueries": metadata["completedQueries"],
                              "plannedQueries": metadata["plannedQueries"],
                              "timeBudgetReached": metadata["timeBudgetReached"],
                              "inventoryVerified": False}, ensure_ascii=False))
        finally:
            await service.client.aclose()
    asyncio.run(collect())


if __name__ == "__main__":
    main()
