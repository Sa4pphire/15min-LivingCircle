"""Build the temporary v2 network from the checked-in frontend road graph.

Run from the repository root:
  python backend/scripts/export_synthetic_preview.py
"""

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.synthetic_converter import convert_preview_graph  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=REPO_ROOT / "data/networks/synthetic-preview.json")
    args = parser.parse_args()
    source = REPO_ROOT / "frontend/src/data/demoRoadGraph.local.json"
    context = REPO_ROOT / "frontend/src/data/demoContext.extended.wgs84.json"
    graph = json.loads(source.read_text(encoding="utf-8"))
    origin = json.loads(context.read_text(encoding="utf-8"))["originWgs84"]
    network = convert_preview_graph(graph, origin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(network, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
    print(f"Synthetic v2 network: {len(network['nodes'])} nodes, "
          f"{len(network['edges'])} edges, "
          f"{len(network['sourceGraph']['syntheticLinkIds'])} unverified links; "
          f"wrote {args.output}")


if __name__ == "__main__":
    main()
