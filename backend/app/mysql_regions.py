"""Immutable region packages in MySQL, loaded outside the request event loop.

JSON is stored as original bytes, not reserialized by MySQL: the existing
manifest hashes, numeric text, coordinate order and authoring data survive.
Each operation owns a short-lived connection. No implicit schema creation,
online edit API, filesystem fallback, or per-edge SQL belongs in this module.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


MAX_FILE_BYTES = 32 * 1024 * 1024
_REGION_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,79}')
_DIGEST = re.compile(r'[0-9a-f]{64}')


class RegionStoreError(RuntimeError):
    """Storage/configuration failure with no driver message or credentials."""


def _identity(region_id: str, version_id: str | None = None) -> None:
    if not isinstance(region_id, str) or not _REGION_ID.fullmatch(region_id):
        raise ValueError('区域标识无效')
    if version_id is not None and (not isinstance(version_id, str) or
                                   not _DIGEST.fullmatch(version_id)):
        raise ValueError('区域内容版本必须为 64 位 SHA256')


def _relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512 or
            any(character in value for character in ('\\', ':', '\x00')) or
            any(part in ('', '.', '..') or part.endswith((' ', '.'))
                for part in value.split('/')) or PurePosixPath(value).is_absolute()):
        raise ValueError('区域包文件必须使用规范的相对路径')
    return value


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_file(path: Path) -> bytes:
    with path.open('rb') as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('区域包单文件不能超过 32 MiB')
    return content


def _json(content: bytes):
    def reject_constant(_value):
        raise ValueError('区域 JSON 不能含 NaN 或 Infinity')
    return json.loads(content.decode('utf-8'), parse_constant=reject_constant)


def _snapshot(manifest_bytes: bytes, files: dict[str, bytes]):
    """Validate portable paths/content and return manifest, roles, content ID."""
    if len(manifest_bytes) > MAX_FILE_BYTES:
        raise ValueError('区域包清单不能超过 32 MiB')
    manifest = _json(manifest_bytes)
    if (not isinstance(manifest, dict) or manifest.get('packageSchemaVersion') != 1 or
            not isinstance(manifest.get('name'), str) or not manifest['name'] or
            not isinstance(manifest.get('version'), str) or
            len(manifest['version']) > 255 or
            not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', manifest['version'])):
        raise ValueError('区域包清单无效')
    _identity(manifest.get('id'))
    if manifest.get('status', 'ready') != 'ready':
        raise ValueError('仅支持导入已完成的区域包，不导入草稿')
    declared, hashes = manifest.get('files'), manifest.get('sha256')
    if (not isinstance(declared, dict) or not {'network', 'context'} <= declared.keys() or
            not isinstance(hashes, dict) or hashes.keys() != declared.keys()):
        raise ValueError('区域清单必须包含路网、底图及全部数据文件的校验和')
    roles = {}
    aliases = {'manifest.json', 'readme.md'}
    for role, relative in declared.items():
        if not isinstance(role, str) or not role or len(role) > 80:
            raise ValueError('区域文件角色无效')
        relative = _relative_path(relative)
        if PurePosixPath(relative).suffix.lower() != '.json' or relative.casefold() in aliases:
            raise ValueError('区域 JSON 文件路径重复或占用清单／README 路径')
        aliases.add(relative.casefold())
        roles[relative] = role
    expected = set(roles)
    if 'README.md' in files:
        expected.add('README.md')
    if set(files) != expected:
        raise ValueError('区域包存储文件与清单不一致')
    records = []
    for relative, content in sorted(files.items()):
        _relative_path(relative)
        if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
            raise ValueError('区域包单文件必须为字节数据且不能超过 32 MiB')
        checksum = _digest(content)
        role = roles.get(relative)
        if role is not None:
            if hashes[role] != checksum:
                raise ValueError(f'区域文件校验和不一致: {role}')
            _json(content)
        records.append([relative, role, checksum])
    identity = {'manifest': _digest(manifest_bytes), 'files': records}
    version_id = _digest(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8'))
    return manifest, roles, version_id


class MySQLRegionRepository:
    def __init__(self) -> None:
        self._host = os.getenv('MYSQL_HOST', '127.0.0.1')
        self._user = os.getenv('MYSQL_USER', 'livingcircle')
        self._database = os.getenv('MYSQL_DATABASE', 'livingcircle')
        self._password = os.getenv('MYSQL_PASSWORD', '')
        try:
            self._port = int(os.getenv('MYSQL_PORT', '3306'))
        except ValueError:
            raise RegionStoreError('MYSQL_PORT 必须为有效端口') from None
        if not 1 <= self._port <= 65535:
            raise RegionStoreError('MYSQL_PORT 必须为有效端口')
        if not all((self._host, self._user, self._database, self._password)):
            raise RegionStoreError('请配置 MYSQL_HOST、MYSQL_USER、MYSQL_DATABASE 和 MYSQL_PASSWORD')

    @contextmanager
    def _connection(self):
        try:
            import pymysql
        except ImportError:
            raise RegionStoreError('请安装 backend/requirements-mysql.txt 中的可选依赖') from None
        connection = None
        try:
            connection = pymysql.connect(
                host=self._host, port=self._port, user=self._user,
                password=self._password, database=self._database,
                charset='utf8mb4', binary_prefix=True,
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False, connect_timeout=5, read_timeout=30, write_timeout=30)
            yield connection
        except pymysql.MySQLError:
            # Closing an uncommitted connection rolls back its transaction.
            # Do not expose SQL, payloads, hostnames or authentication details.
            raise RegionStoreError('MySQL 区域存储操作失败，请检查连接、权限和表结构') from None
        finally:
            if connection is not None:
                connection.close()

    def initialize_schema(self) -> None:
        """Explicit administrative DDL; MySQL DDL is not one rollback unit."""
        schema = Path(__file__).resolve().parents[1] / 'sql' / '001_region_packages.sql'
        with self._connection() as connection, connection.cursor() as cursor:
            # This fixed migration contains only CREATE TABLE statements.
            for statement in schema.read_text(encoding='utf-8').split(';'):
                if statement.strip():
                    cursor.execute(statement)
            connection.commit()

    def list_published(self) -> list[dict[str, str]]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT region_id, current_version_id AS version_id FROM regions '
                           'WHERE current_version_id IS NOT NULL ORDER BY region_id')
            return list(cursor.fetchall())

    def load_version(self, region_id: str, version_id: str) -> tuple[bytes, dict[str, bytes]]:
        _identity(region_id, version_id)
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT manifest_bytes, manifest_sha256 FROM region_versions '
                           'WHERE region_id=%s AND version_id=%s', (region_id, version_id))
            row = cursor.fetchone()
            if row is None:
                raise FileNotFoundError('指定区域版本不存在')
            manifest_bytes = bytes(row['manifest_bytes'])
            if _digest(manifest_bytes) != row['manifest_sha256']:
                raise RegionStoreError('数据库区域清单校验和不一致')
            cursor.execute('SELECT relative_path, file_role, sha256, payload FROM region_files '
                           'WHERE region_id=%s AND version_id=%s ORDER BY relative_path',
                           (region_id, version_id))
            files, stored_roles = {}, {}
            for item in cursor.fetchall():
                content = bytes(item['payload'])
                if _digest(content) != item['sha256']:
                    raise RegionStoreError('数据库区域文件校验和不一致')
                files[item['relative_path']] = content
                stored_roles[item['relative_path']] = item['file_role']
        manifest, roles, calculated = _snapshot(manifest_bytes, files)
        if (manifest['id'] != region_id or calculated != version_id or
                stored_roles != {path: roles.get(path) for path in files}):
            raise RegionStoreError('数据库区域内容与版本标识不一致')
        return manifest_bytes, files

    def import_directory(self, directory: Path, *, publish: bool = False) -> dict:
        from .region_package import load_region

        directory = Path(directory).resolve()
        region = load_region(directory)
        metadata = region.public_metadata()
        if not metadata['nodeCount'] or not metadata['edgeCount']:
            raise ValueError('不能导入尚无路网的区域包')
        manifest_bytes = _read_file(directory / 'manifest.json')
        if _json(manifest_bytes) != region.manifest:
            raise ValueError('读取期间区域清单发生变化，请停止编辑后重试')
        files = {_relative_path(relative): _read_file(region.file(role))
                 for role, relative in region.manifest['files'].items()}
        readme = directory / 'README.md'
        if readme.exists():
            if not readme.resolve().is_relative_to(directory):
                raise ValueError('README 必须位于区域包目录内')
            files['README.md'] = _read_file(readme)
        manifest, roles, version_id = _snapshot(manifest_bytes, files)
        region_id = manifest['id']
        graph = _json(files[manifest['files']['network']])
        context = _json(files[manifest['files']['context']])
        origin_key = 'originWgs84' if 'originWgs84' in graph else 'originBd09'
        if (graph.get('regionId', region_id) != region_id or
                not isinstance(context, dict) or
                context.get(origin_key) != [graph[origin_key]['lng'], graph[origin_key]['lat']]):
            raise ValueError('区域路网、底图的区域标识或坐标原点不一致')

        with self._connection() as connection, connection.cursor() as cursor:
            # Serialize imports/publications for this region while retaining
            # every immutable old version. No replace/delete of graph rows.
            cursor.execute('INSERT INTO regions (region_id) VALUES (%s) '
                           'ON DUPLICATE KEY UPDATE region_id=region_id', (region_id,))
            cursor.execute('SELECT manifest_sha256 FROM region_versions '
                           'WHERE region_id=%s AND version_id=%s', (region_id, version_id))
            previous = cursor.fetchone()
            created = previous is None
            if created:
                cursor.execute('INSERT INTO region_versions '
                               '(region_id,version_id,package_version,manifest_sha256,manifest_bytes) '
                               'VALUES (%s,%s,%s,%s,%s)',
                               (region_id, version_id, manifest['version'],
                                _digest(manifest_bytes), manifest_bytes))
                # One file per INSERT avoids assembling the whole package into
                # one SQL packet. Raw bytes may expand during driver escaping.
                for relative, content in files.items():
                    cursor.execute('INSERT INTO region_files '
                                   '(region_id,version_id,relative_path,file_role,sha256,payload) '
                                   'VALUES (%s,%s,%s,%s,%s,%s)',
                                   (region_id, version_id, relative, roles.get(relative),
                                    _digest(content), content))
            elif previous['manifest_sha256'] != _digest(manifest_bytes):
                raise RegionStoreError('数据库已有内容版本与导入清单不一致')
            if publish:
                cursor.execute('UPDATE regions SET current_version_id=%s WHERE region_id=%s',
                               (version_id, region_id))
            connection.commit()
        return {'region_id': region_id, 'version_id': version_id,
                'package_version': manifest['version'], 'created': created, 'published': publish}

    def publish(self, region_id: str, version_id: str) -> None:
        _identity(region_id, version_id)
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT version_id FROM region_versions '
                           'WHERE region_id=%s AND version_id=%s', (region_id, version_id))
            if cursor.fetchone() is None:
                raise FileNotFoundError('指定区域版本不存在')
            cursor.execute('INSERT INTO regions (region_id,current_version_id) VALUES (%s,%s) '
                           'ON DUPLICATE KEY UPDATE current_version_id=%s',
                           (region_id, version_id, version_id))
            connection.commit()
