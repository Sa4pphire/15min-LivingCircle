"""Once-only official coordinate conversion for the frontend's fixed map."""

import argparse
import asyncio
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.baidu.client import BaiduClient  # noqa: E402
from app.baidu.errors import BaiduApiError  # noqa: E402
from app.map_assets import export_map_asset  # noqa: E402


async def run(args) -> None:
    client = BaiduClient()
    try:
        asset, updated = await export_map_asset(args.source, args.output, client, refresh=args.refresh)
        print(f"{'Generated' if updated else 'Unchanged'} BD-09 map asset: "
              f"{len(asset['boundary']['geometry']['coordinates'][0])} boundary points; "
              f"{len(asset['alignment']['pointsBd09'])} alignment anchors; "
              f"{client.cache_stats['apiRequests']} API requests. Output: {args.output}")
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=REPO_ROOT / "frontend/src/data/demoBoundary.wgs84.json")
    parser.add_argument("--output", type=Path,
                        default=REPO_ROOT / "frontend/src/data/demoMap.bd09.json")
    parser.add_argument("--refresh", action="store_true", help="Explicitly reconvert using the official API")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (BaiduApiError, ValueError, OSError) as exc:
        # Client errors are sanitized. Never print a request URL, AK or traceback.
        parser.exit(1, f"Map asset not updated: {exc}\n")


if __name__ == "__main__":
    main()
