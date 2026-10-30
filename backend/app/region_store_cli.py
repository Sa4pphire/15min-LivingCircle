"""Explicit region package administration, independent of the HTTP app lifecycle."""
import argparse
import json
from pathlib import Path
import sys

# Load the application's .env convention without starting the API or snapshot set.
from .settings import settings  # noqa: F401
from .mysql_regions import MySQLRegionRepository, RegionStoreError
from .region_storage import write_package


def main() -> int:
    parser = argparse.ArgumentParser(description='MySQL 区域包版本导入、发布和导出')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='显式创建首版数据表；不导入数据')
    importer = commands.add_parser('import', help='导入已校验区域目录，默认不发布')
    importer.add_argument('directory', type=Path)
    importer.add_argument('--publish', action='store_true')
    publisher = commands.add_parser('publish', help='切换发布指针；API 重启后生效')
    publisher.add_argument('region_id')
    publisher.add_argument('version_id')
    commands.add_parser('list', help='列出数据库当前发布指针')
    exporter = commands.add_parser('export', help='导出指定完整版本到一个不存在的目录')
    exporter.add_argument('region_id')
    exporter.add_argument('version_id')
    exporter.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        repository = MySQLRegionRepository()
        if args.command == 'init':
            repository.initialize_schema()
            result = {'schema': 1, 'initialized': True}
        elif args.command == 'import':
            result = repository.import_directory(args.directory, publish=args.publish)
        elif args.command == 'publish':
            repository.publish(args.region_id, args.version_id)
            result = {'region_id': args.region_id, 'version_id': args.version_id,
                      'published': True, 'requiresBackendRestart': True}
        elif args.command == 'list':
            result = {'published': repository.list_published()}
        else:
            manifest, files = repository.load_version(args.region_id, args.version_id)
            write_package(args.directory.resolve(), manifest, files)
            result = {'region_id': args.region_id, 'version_id': args.version_id,
                      'exportedTo': str(args.directory.resolve())}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (RegionStoreError, OSError, ValueError, KeyError, TypeError) as exc:
        # Repository errors are sanitized, never print a driver traceback/DSN.
        print(f'区域包操作失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
