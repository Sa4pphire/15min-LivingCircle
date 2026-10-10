"""Bundle existing local POI responses into a portable region; never fetch APIs."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.region_package import load_region
from app.region_pois import collect_cached_pois
from app.settings import settings


def bundle_region(region, cache_dir):
    document = collect_cached_pois(region, Path(cache_dir))
    data = (json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    manifest = deepcopy(region.manifest)
    relative = manifest['files'].get('pois', 'pois.json')
    manifest['files']['pois'] = relative
    manifest['sha256']['pois'] = hashlib.sha256(data).hexdigest()
    manifest['facilityData'] = {**manifest.get('facilityData', {}),
        'candidatePois': 'bundled-snapshot', 'candidateCount': len(document['items']),
        'inventoryVerified': False, 'accessVerified': False}
    target = region.root / relative
    if target.parent != region.root or target.suffix != '.json':
        raise ValueError('POI 快照必须为区域根目录中的 JSON 文件')
    manifest_path = region.root / 'manifest.json'
    before_manifest = manifest_path.read_bytes()
    before_pois = target.read_bytes() if target.exists() else None
    backup = ROOT / 'data/cache/region-poi-backups' / (region.manifest['id'] + '-' +
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True)
    (backup / 'manifest.json').write_bytes(before_manifest)
    if before_pois is not None:
        (backup / 'pois.json').write_bytes(before_pois)
    pending = target.with_suffix('.json.tmp')
    pending_manifest = manifest_path.with_suffix('.json.tmp')
    try:
        pending.write_bytes(data)
        pending_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        pending.replace(target)
        pending_manifest.replace(manifest_path)
        load_region(region.root)
    except Exception:
        manifest_path.write_bytes(before_manifest)
        if before_pois is not None:
            target.write_bytes(before_pois)
        elif target.exists():
            target.unlink()
        raise
    finally:
        pending.unlink(missing_ok=True)
        pending_manifest.unlink(missing_ok=True)
    return {'regionId': manifest['id'], 'poiCount': len(document['items']),
            'categoryCounts': document['categoryCounts'], 'bytes': len(data),
            'apiRequests': 0, 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region-id', default=settings.region_id)
    parser.add_argument('--cache-dir', type=Path, default=settings.analysis_cache_dir)
    args = parser.parse_args()
    from app.region_package import load_region_by_id
    print(json.dumps(bundle_region(load_region_by_id(args.region_id), args.cache_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
