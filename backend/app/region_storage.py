"""MySQL is authoritative; each app lifetime reads one fixed package release set.

Materialized JSON files are a disposable adapter for existing graph, calibration
and asset readers. They are never edited or used as an offline startup fallback.
"""
from dataclasses import replace
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .region_package import RegionPackage, load_region
from .settings import settings


_directory: TemporaryDirectory | None = None
_regions: dict[str, RegionPackage] | None = None


def write_package(directory: Path, manifest_bytes: bytes,
                  files: dict[str, bytes]) -> RegionPackage:
    """Write one newly-owned directory and validate the complete package."""
    manifest = json.loads(manifest_bytes)
    declared = manifest.get('files')
    if not isinstance(declared, dict):
        raise ValueError('数据库区域清单缺少文件列表')
    expected = set(declared.values())
    if set(files) not in (expected, expected | {'README.md'}):
        raise ValueError('数据库区域文件列表与清单不一致')
    if len(expected) != len(declared) or 'manifest.json' in expected:
        raise ValueError('数据库区域文件路径重复或覆盖清单')
    # Use a fresh directory: never overwrite an existing export or authoring tree.
    directory.mkdir(parents=True, exist_ok=False)
    for relative, content in files.items():
        if not isinstance(relative, str):
            raise ValueError('数据库区域文件路径无效')
        path = (directory / relative).resolve()
        if (Path(relative).is_absolute() or '\\' in relative or
                not path.is_relative_to(directory.resolve()) or
                any(part in ('', '.', '..') for part in relative.split('/'))):
            raise ValueError('数据库区域文件必须位于新快照目录内')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (directory / 'manifest.json').write_bytes(manifest_bytes)
    region = load_region(directory)
    metadata = region.public_metadata()
    if (metadata['status'] != 'ready' or metadata['nodeCount'] == 0 or
            metadata['edgeCount'] == 0):
        raise ValueError('数据库仅支持发布完整可展示的区域包')
    return region


def initialize_region_storage() -> None:
    """Called once before accepting requests; SQL and disk I/O run in a worker."""
    global _directory, _regions
    if settings.region_storage_backend == 'file':
        return
    from .mysql_regions import MySQLRegionRepository
    repository = MySQLRegionRepository()
    releases = repository.list_published()
    if not any(entry['region_id'] == settings.region_id for entry in releases):
        raise ValueError('MySQL 中尚未发布默认区域，请先导入并发布 REGION_ID')
    if _directory is not None:
        raise RuntimeError('区域快照已经初始化')
    owner = TemporaryDirectory(prefix='livingcircle-regions-')
    regions = {}
    try:
        for entry in releases:
            identity, version = entry['region_id'], entry['version_id']
            if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', identity) or
                    not re.fullmatch(r'[a-f0-9]{64}', version)):
                raise ValueError('数据库区域版本标识无效')
            manifest, files = repository.load_version(identity, version)
            region = write_package(Path(owner.name) / identity, manifest, files)
            if region.manifest['id'] != identity:
                raise ValueError('数据库区域标识与清单不一致')
            regions[identity] = replace(region, storage_backend='mysql', storage_version=version)
        # Publish only after every selected package has loaded successfully.
        _directory, _regions = owner, regions
    except BaseException:
        owner.cleanup()
        raise


def stored_region(identity: str) -> RegionPackage:
    if _regions is None:
        raise RuntimeError('MySQL 区域快照尚未初始化')
    if identity not in _regions:
        raise FileNotFoundError('该区域不在本次服务启动时的已发布列表中')
    return _regions[identity]


def stored_regions() -> tuple[RegionPackage, ...]:
    if _regions is None:
        raise RuntimeError('MySQL 区域快照尚未初始化')
    return tuple(_regions[key] for key in sorted(_regions))


def close_region_storage() -> None:
    """Called after admitted calculations and their readers have stopped."""
    global _directory, _regions
    owner = _directory
    _directory, _regions = None, None
    if owner is not None:
        owner.cleanup()
