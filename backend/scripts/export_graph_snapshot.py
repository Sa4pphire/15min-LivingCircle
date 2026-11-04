"""Export one C++ fixed graph plus its Python request/calibration companion.

Writes only explicit, new output paths; never starts the engine or changes .env.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.fixed_graph import export_snapshot_documents
from app.region_package import load_region


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--region-dir", type=Path)
    source.add_argument("--network", type=Path)
    parser.add_argument("--region-id")
    parser.add_argument("--alignment", type=Path)
    parser.add_argument("--pois", type=Path)
    parser.add_argument("--source-package-version-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.region_dir and any(value is not None for value in
                               (args.region_id, args.alignment, args.pois)):
        parser.error("--region-dir 使用包内 ID/校准/POI，不能与对应文件模式选项混用")
    if args.network and not args.region_id:
        parser.error("--network 必须同时提供 --region-id")
    output = args.output.resolve()
    companion_path = output.with_suffix(".context.json")
    if output == companion_path or output.exists() or companion_path.exists():
        parser.error("输出文件必须为互不相同的两个新路径，不能覆盖现有文件")
    if args.region_dir:
        region = load_region(args.region_dir)
        network_path, region_id = region.file("network"), region.manifest["id"]
        alignment_path = region.file("alignment") if "alignment" in region.manifest["files"] else None
        pois_path = region.file("pois") if "pois" in region.manifest["files"] else None
    else:
        network_path, region_id = args.network, args.region_id
        alignment_path, pois_path = args.alignment, args.pois
    network_bytes = network_path.read_bytes()
    network = json.loads(network_bytes.decode("utf-8"))
    if not isinstance(network, dict):
        raise ValueError("源路网必须为 JSON 对象")
    alignment = json.loads(alignment_path.read_text("utf-8")) if alignment_path else None
    pois = json.loads(pois_path.read_text("utf-8")) if pois_path else None
    artifact, context, graph_id = export_snapshot_documents(
        network, region_id=region_id,
        network_revision=hashlib.sha256(network_bytes).hexdigest(),
        source_package_version_id=args.source_package_version_id,
        alignment=alignment, pois=pois)
    # All preparation precedes publication. Exclusive create also protects
    # existing files if a path appeared after argument validation. A failed
    # write can leave an incomplete pair; neither server starts from this CLI.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(artifact)
    with companion_path.open("xb") as stream:
        stream.write(context)
    print(json.dumps({"graphId": graph_id, "artifact": str(output),
                      "context": str(companion_path), "regionId": region_id,
                      "sourceBytes": len(artifact), "contextBytes": len(context)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
