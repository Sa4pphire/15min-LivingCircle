"""Build the temporary v2 network from the checked-in frontend road graph.

Run from the repository root:
  python backend/scripts/export_synthetic_preview.py
"""

import argparse
import json
from pathlib import Path
import sys
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.manual_graph_edits import apply_manual_edits  # noqa: E402
from app.region_package import load_region, network_bounds, refresh_manifest  # noqa: E402
from app.network_editor import build_base_network  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", action="store_true", help="Use the pre-package authoring files")
    parser.add_argument("--region-id", help="Rebuild a selected region package without changing the active region")
    parser.add_argument("--output", type=Path,
                        default=None)
    parser.add_argument("--annotations", type=Path,
                        default=None)
    args = parser.parse_args()
    if args.region_id and (args.legacy or not re.fullmatch(r'[a-z0-9_-]+', args.region_id)):
        raise ValueError('--region-id 需要有效标识，且不能与 --legacy 一起使用')
    region = None if args.legacy else load_region(REPO_ROOT/'data/regions'/args.region_id) if args.region_id else load_region()
    source = region.file('sourceGraph') if region else REPO_ROOT / "frontend/src/data/demoRoadGraph.local.json"
    context = region.file('context') if region else REPO_ROOT / "frontend/src/data/demoContext.extended.wgs84.json"
    args.annotations = args.annotations or (region.file('annotations') if region else
        REPO_ROOT / "data/networks/synthetic-preview.annotations.json")
    args.output = args.output or (region.file('network') if region else
        REPO_ROOT / "data/networks/synthetic-preview.json")
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    if (annotations.get("schemaVersion") != 1 or
            annotations.get("coordinateSystem") != "engine-local-meters" or
            not isinstance(annotations.get("crossings"), list) or
            not isinstance(annotations.get("junctions", []), list)):
        raise ValueError("invalid synthetic crossing annotation file")
    network = build_base_network(source, context, annotations)
    network = apply_manual_edits(network, annotations.get("manualGraphEdits"))
    if region:
        network['selectionSource'] = 'network-path-extent'
        network['regionId'] = region.manifest['id']
        network['supportedCenterBoundsMeters'] = network_bounds(network)
        network.pop('supportedCenterPolygonMeters', None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(network, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
    if region and args.output.resolve() == region.file('network').resolve():
        refresh_manifest(region)
    print(f"Synthetic v2 network: {len(network['nodes'])} nodes, "
          f"{len(network['edges'])} edges, "
          f"{len(network['sourceGraph'].get('syntheticLinkIds', []))} source inferred links; "
          f"wrote {args.output}")


if __name__ == "__main__":
    main()
