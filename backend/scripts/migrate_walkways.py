"""Unify walking kinds without editing a single node, path or connection.

Run without --write to inspect; --write migrates the current edited network,
its replayable manual patch and contract examples. Source-road geometry stays
untouched. A local backup is always created before applying the migration.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.manual_graph_edits import apply_manual_edits, graph_revision, make_manual_edits, validate_editor_graph
from app.synthetic_converter import convert_preview_graph, with_preview_annotations
from app.walking_types import legacy_graph_view, normalize_edge, normalize_graph


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fingerprint(graph):
    # Include every existing node/edge property, except the two migrated fields.
    value = {"nodes": graph["nodes"], "edges": [
        {key: value for key, value in edge.items() if key not in ("kind", "accessMode")}
        for edge in graph["edges"]]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def migrate_example(value):
    if isinstance(value, list):
        return [migrate_example(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: migrate_example(item) for key, item in value.items()}
    if result.get("kind") in ("sidewalk", "shared_way"):
        if all(key in result for key in ("from", "to", "pathMeters")):
            return normalize_edge(result)
        result["kind"] = "walkway"  # Output segments do not carry access policy.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    network_path = ROOT / "data/networks/synthetic-preview.json"
    annotation_path = ROOT / "data/networks/synthetic-preview.annotations.json"
    original, annotations = read(network_path), read(annotation_path)
    raw = read(ROOT / "frontend/src/data/demoRoadGraph.local.json")
    origin = read(ROOT / "frontend/src/data/demoContext.extended.wgs84.json")["originWgs84"]
    base = convert_preview_graph(with_preview_annotations(raw, annotations), origin,
                                 annotations["crossings"], annotations.get("junctions", []))
    existing_patch = annotations.get("manualGraphEdits")
    if existing_patch:
        # Permit exactly the old type representation or the new canonical one.
        if existing_patch["baseGraphRevision"] not in (
                graph_revision(base), graph_revision(legacy_graph_view(base))):
            raise ValueError("source graph changed; manual edits require review before migration")
        check_patch = {**existing_patch, "baseGraphRevision": graph_revision(base)}
        if graph_revision(apply_manual_edits(base, check_patch)) != graph_revision(normalize_graph(original)):
            raise ValueError("stored network differs from replayed manual edits; refusing to overwrite")
    elif graph_revision(base) != graph_revision(normalize_graph(original)):
        raise ValueError("stored network differs from source graph; refusing to overwrite")
    migrated = normalize_graph(original)
    validate_editor_graph(migrated)
    updated_annotations = deepcopy(annotations)
    if existing_patch:
        patch = {**existing_patch, **make_manual_edits(base, migrated)}
        for key in ("removedNodes", "removedEdges"):
            if patch[key] != existing_patch[key]:
                raise ValueError("migration would change the manual removal plan")
        for key in ("upsertNodes", "upsertEdges"):
            if {item["id"] for item in patch[key]} != {item["id"] for item in existing_patch[key]}:
                raise ValueError("migration would change the set of manually edited records")
        updated_annotations["manualGraphEdits"] = patch
        replayed = apply_manual_edits(base, patch)
        if graph_revision(replayed) != graph_revision(migrated):
            raise ValueError("canonical manual edits cannot reproduce the current network")
        migrated.setdefault("sourceGraph", {})["manualGraphEdits"] = replayed["sourceGraph"]["manualGraphEdits"]
    if fingerprint(original) != fingerprint(migrated):
        raise ValueError("migration would change IDs, geometry, connectivity or edge properties")
    pending = {}
    if migrated != original:
        pending[network_path] = migrated
    if updated_annotations != annotations:
        pending[annotation_path] = updated_annotations
    for path in (ROOT / "contracts").glob("*.json"):
        old = read(path)
        new = migrate_example(old)
        if old != new:
            pending[path] = new
    print(json.dumps({"nodes": len(migrated["nodes"]), "edges": len(migrated["edges"]),
                      "unchangedGeometryAndTopology": fingerprint(migrated),
                      "files": [str(path.relative_to(ROOT)) for path in pending]}, ensure_ascii=False))
    if args.write and pending:
        backup = ROOT / "data/cache/walkway-migration-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup.mkdir(parents=True)
        for path in pending:
            saved = backup / path.relative_to(ROOT)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(path.read_bytes())
        try:
            for path, value in pending.items():
                path.write_text(json.dumps(value, ensure_ascii=False,
                    separators=(",", ":") if path == network_path else None,
                    indent=None if path == network_path else 2) + ("" if path == network_path else "\n"), encoding="utf-8")
        except OSError:
            for path in pending:
                path.write_bytes((backup / path.relative_to(ROOT)).read_bytes())
            raise
        print("Applied; local backup:", backup)


if __name__ == "__main__":
    main()
