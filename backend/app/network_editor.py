"""Local development editor for the authoritative synthetic annotations."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from importlib.util import find_spec
import ipaddress
import json
import os
from pathlib import Path
import threading
import re
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .engine import EngineBusy, EngineError, run_engine
from .manual_graph_edits import (apply_manual_edits, graph_revision,
                                 make_manual_edits, validate_editor_graph)
from .settings import REPO_ROOT, settings
from .synthetic_converter import convert_preview_graph, with_preview_annotations
from .region_package import load_region, network_bounds, refresh_manifest
from .walking_types import normalize_graph, is_walkway
from .region_export import crop_graph, create_region, region_zip
from .region_creation import create_blank_region
from .region_context import generate_context
from .baidu.errors import BaiduApiError
from .junction_generation import generate_network_junctions
from .refinement_inputs import build_refinement_inputs, apply_refinement_decision
from .refinement_impact import plan_refinements
from .region_pois import read_region_pois
from .mysql_regions import MySQLRegionRepository, RegionStoreError

router = APIRouter(prefix='/api/v1/network-editor', tags=['local-network-editor'])
_lock = threading.RLock()


class EditorGraph(BaseModel):
    revision: str = Field(min_length=1, max_length=100)
    nodes: list[dict] = Field(max_length=30000)
    edges: list[dict] = Field(max_length=50000)


class RegionPublication(BaseModel):
    # Publishing uses the saved package, never a submitted canvas snapshot.
    model_config = {'extra': 'forbid'}
    revision: str = Field(min_length=1, max_length=100)


class RefinementPlan(EditorGraph):
    originNodeId: str = Field(min_length=1, max_length=512)
    budget: int = Field(default=10, ge=1, le=30)
    lowWaitSeconds: float = Field(default=0, ge=0, le=300, allow_inf_nan=False)
    highWaitSeconds: float = Field(default=60, ge=0, le=300, allow_inf_nan=False)


class RefinementDecision(EditorGraph):
    inputId: str = Field(min_length=1, max_length=128)
    decision: Literal['open', 'closed', 'set_wait']
    waitSeconds: float = Field(default=20, ge=0, le=300, allow_inf_nan=False)
    evidenceNote: str = Field(min_length=5, max_length=1000)


class NewRegion(BaseModel):
    location: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    centerBd09: list[float] = Field(min_length=2, max_length=2)


class PackageSelection(EditorGraph):
    boundsMeters: dict[str, float]


class ContextSelection(BaseModel):
    revision: str = Field(min_length=1, max_length=100)
    boundsMeters: dict[str, float]


class ContextSave(BaseModel):
    revision: str = Field(min_length=1, max_length=100)
    previewId: str = Field(pattern=r'^[a-f0-9]{32}$')


class PackageExport(PackageSelection):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,79}$')
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(default='0.1.0', max_length=32, pattern=r'^\d+\.\d+\.\d+$')


def editor_region(region_id: str | None = None, root: Path | None = None):
    root = root or REPO_ROOT
    identity = region_id or settings.region_id
    if not re.fullmatch(r'[a-z0-9_-]+', identity):
        raise ValueError('区域标识无效')
    region = load_region(root / 'data/regions' / identity)
    if region.manifest['id'] != identity:
        raise ValueError('区域目录名与清单标识不一致')
    return region


def editor_paths(root: Path = REPO_ROOT, region_id: str | None = None) -> tuple[Path, Path, Path, Path]:
    package = root / 'data/regions' / (region_id or settings.region_id)
    if region_id:
        region = editor_region(region_id, root)
        return tuple(region.file(key) for key in ('network', 'annotations', 'sourceGraph', 'context'))
    if (package / 'manifest.json').is_file():
        region = load_region(package)
        return tuple(region.file(key) for key in ('network', 'annotations', 'sourceGraph', 'context'))
    return (root / 'data/networks/synthetic-preview.json',
            root / 'data/networks/synthetic-preview.annotations.json',
            root / 'frontend/src/data/demoRoadGraph.local.json',
            root / 'frontend/src/data/demoContext.extended.wgs84.json')


def file_revision(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_base_network(source: Path, context: Path, annotations: dict) -> dict:
    original = json.loads(source.read_text(encoding='utf-8'))
    if annotations.get('authoringMode') == 'engine-snapshot':
        network = normalize_graph(original)
    else:
        graph = with_preview_annotations(original, annotations)
        origin = json.loads(context.read_text(encoding='utf-8'))['originWgs84']
        network = convert_preview_graph(graph, origin, annotations['crossings'], annotations.get('junctions', []))
    if (context.parent / 'manifest.json').is_file():
        network['selectionSource'] = 'network-path-extent'
        network['regionId'] = load_region(context.parent).manifest['id']
        network['supportedCenterBoundsMeters'] = network_bounds(network)
        network.pop('supportedCenterPolygonMeters', None)
    return network


def _local_access(request: Request) -> None:
    host = request.client.host if request.client else ''
    try:
        address = ipaddress.ip_address(host)
        local = address.is_loopback
        # Docker's host connection arrives from its private bridge. This opt-in
        # is enabled only by the Compose config publishing on 127.0.0.1.
        if (not local and settings.local_container_editor and address.is_private and
                request.url.hostname in ('localhost', '127.0.0.1', '::1')):
            local = True
    except ValueError:
        local = host == 'testclient'
    origin = request.headers.get('origin')
    if (not local or not settings.network_editor_enabled or
            request.headers.get('sec-fetch-site') == 'cross-site' or
            (origin and urlparse(origin).hostname not in ('localhost', '127.0.0.1', '::1'))):
        raise HTTPException(403, detail={'message': '路网编辑仅支持本机开发服务'})


def read_editor(root: Path = REPO_ROOT, region_id: str | None = None) -> dict:
    with _lock:
        paths = editor_paths(root, region_id)
        graph = normalize_graph(json.loads(paths[0].read_text(encoding='utf-8')))
        return {'graph': graph, 'revision': file_revision(paths),
                'audit': validate_editor_graph(graph)}


def candidate_graph(payload: EditorGraph, root: Path = REPO_ROOT, region_id: str | None = None) -> dict:
    current = read_editor(root, region_id)
    if current['revision'] != payload.revision:
        raise HTTPException(409, detail={'message': '文件已被其他窗口或脚本修改，请导出草稿后重新加载'})
    graph = normalize_graph({**current['graph'], 'nodes': payload.nodes, 'edges': payload.edges})
    validate_editor_graph(graph)
    return graph


def _encoded(value: dict, *, pretty: bool = False) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      indent=2 if pretty else None,
                      separators=None if pretty else (',', ':')).encode('utf-8')


def _replace(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + '.editor-' + uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_editor(payload: EditorGraph, root: Path = REPO_ROOT, region_id: str | None = None) -> dict:
    with _lock:
        graph = candidate_graph(payload, root, region_id)
        network_path, annotation_path, source, context = editor_paths(root, region_id)
        annotations = json.loads(annotation_path.read_text(encoding='utf-8'))
        base = build_base_network(source, context, annotations)
        replayed = apply_manual_edits(base, annotations.get('manualGraphEdits'))
        if graph_revision(replayed) != graph_revision(json.loads(network_path.read_text(encoding='utf-8'))):
            raise HTTPException(409, detail={'message': '生成路网与源标注不一致，请先运行路网导出脚本再重新加载'})
        patch = make_manual_edits(base, graph)
        annotations = deepcopy(annotations)
        annotations['manualGraphEdits'] = patch
        regenerated = apply_manual_edits(base, patch)
        regenerated['supportedCenterBoundsMeters'] = network_bounds(regenerated)
        manifest_path = network_path.parent / 'manifest.json'
        files_to_save = [annotation_path, network_path]
        if manifest_path.is_file():
            files_to_save.append(manifest_path)
        before = {p: p.read_bytes() for p in files_to_save}
        backup = root / 'data/cache/network-editor-backups' / (
            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8])
        backup.mkdir(parents=True)
        for path, content in before.items():
            (backup / path.name).write_bytes(content)
        # Detect changes made by another process while preparing the save.
        if file_revision(editor_paths(root, region_id)) != payload.revision:
            raise HTTPException(409, detail={'message': '保存期间源文件发生变化，请导出草稿后重新加载'})
        try:
            _replace(annotation_path, _encoded(annotations, pretty=True))
            _replace(network_path, _encoded(regenerated))
            if manifest_path.is_file():
                refresh_manifest(load_region(network_path.parent, verify=False))
        except (OSError, ValueError):
            for path, content in before.items():
                _replace(path, content)
            raise
        result = read_editor(root, region_id)
        result['backup'] = str(backup.relative_to(root))
        return result


def _bad_data(exc: Exception) -> HTTPException:
    return HTTPException(422, detail={'message': f'路网检查失败：{exc}'})


def publication_configuration() -> dict:
    """Inspect local configuration only; this does not open a SQL connection."""
    try:
        MySQLRegionRepository()
    except RegionStoreError as exc:
        return {'configured': False, 'message': str(exc)}
    if find_spec('pymysql') is None or find_spec('cryptography') is None:
        return {'configured': False,
                'message': '请先在后端安装 backend/requirements-mysql.txt 中的可选依赖'}
    return {'configured': True,
            'message': '数据库配置与依赖已准备；连接和数据表将在发布时检查。请先保存本地修改。'}


def publish_saved_region(payload: RegionPublication, root: Path = REPO_ROOT,
                         region_id: str | None = None) -> dict:
    """Import and publish the saved complete package without changing local files."""
    configuration = publication_configuration()
    if not configuration['configured']:
        raise HTTPException(503, detail={'message': configuration['message']})
    # Reuse the editor's save lock so its own file writes cannot interleave with
    # reading a package for publication. SQL work runs in the endpoint's worker.
    with _lock:
        saved_revision = read_editor(root, region_id)['revision']
        if saved_revision != payload.revision:
            raise HTTPException(409, detail={
                'message': '本地区域包已变化，请重新加载并确认保存内容后再发布'})
        region = editor_region(region_id, root)
        result = MySQLRegionRepository().import_directory(region.root, publish=True)
        return {'regionId': result['region_id'], 'versionId': result['version_id'],
                'packageVersion': result['package_version'], 'created': result['created'],
                'published': True, 'requiresBackendRestart': True,
                'revision': saved_revision,
                'message': '已发布完整区域包。读取 MySQL 的展示后端重启后使用此版本；本地编辑服务无需切换模式。'}


@router.get('/publication')
def get_publication(request: Request) -> dict:
    _local_access(request)
    return publication_configuration()


@router.post('/publication')
async def post_publication(request: Request, payload: RegionPublication,
                           regionId: str | None = None) -> dict:
    _local_access(request)
    try:
        return await run_in_threadpool(publish_saved_region, payload, REPO_ROOT, regionId)
    except RegionStoreError as exc:
        # Repository errors deliberately omit driver SQL and credentials.
        raise HTTPException(503, detail={'message': str(exc)}) from None
    except OSError:
        raise HTTPException(422, detail={
            'message': '无法读取完整本地区域包，请检查文件是否齐全及可读取'}) from None
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail={'message': f'区域包校验失败：{exc}'}) from None


def generate_editor_preview(payload: EditorGraph, root: Path = REPO_ROOT,
                            region_id: str | None = None) -> dict:
    """Build a draft from the submitted canvas; saving remains the existing PUT."""
    with _lock:
        graph = candidate_graph(payload, root, region_id)
        paths = editor_paths(root, region_id)
        source = json.loads(paths[2].read_text(encoding='utf-8'))
        annotations = json.loads(paths[1].read_text(encoding='utf-8'))
        if source.get('coordinateSystem') != 'preview-local-v1':
            raise ValueError('当前区域没有可识别的 OSM 道路源图；空白区域请先导入道路源数据')
        result = generate_network_junctions(source, annotations, graph)
        result['audit'] = validate_editor_graph(result['graph'])
        result['revision'] = payload.revision
        if file_revision(paths) != payload.revision:
            raise HTTPException(409, detail={'message': '生成期间源文件已变化，请重新加载后生成'})
        return result


@router.post('/generation/preview')
async def preview_generation(request: Request, payload: EditorGraph,
                             regionId: str | None = None) -> dict:
    _local_access(request)
    try:
        return await run_in_threadpool(generate_editor_preview, payload, REPO_ROOT, regionId)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


def _refinement_inputs(graph: dict, root: Path, region_id: str | None) -> list[dict]:
    paths = editor_paths(root, region_id)
    source = json.loads(paths[2].read_text(encoding='utf-8'))
    annotations = json.loads(paths[1].read_text(encoding='utf-8'))
    region = editor_region(region_id, root)
    inventory = read_region_pois(region)
    return build_refinement_inputs(graph, source, annotations,
                                   inventory['items'] if inventory is not None else None)


def plan_editor_refinement(payload: RefinementPlan, root: Path = REPO_ROOT,
                           region_id: str | None = None) -> dict:
    with _lock:
        graph = candidate_graph(payload, root, region_id)
        inputs = _refinement_inputs(graph, root, region_id)
        result = plan_refinements(graph, inputs, payload.originNodeId,
                                  budget=payload.budget, low_wait_seconds=payload.lowWaitSeconds,
                                  high_wait_seconds=payload.highWaitSeconds)
        if file_revision(editor_paths(root, region_id)) != payload.revision:
            raise HTTPException(409, detail={'message': '评估期间源文件已变化，请重新加载后评估'})
        return {**result, 'revision': payload.revision, 'canvasRevision': graph_revision(graph),
                'inputModelVersion': 1, 'coordinateSystem': 'engine-local-meters'}


def apply_editor_refinement(payload: RefinementDecision, root: Path = REPO_ROOT,
                            region_id: str | None = None) -> dict:
    with _lock:
        graph = candidate_graph(payload, root, region_id)
        draft = apply_refinement_decision(graph, _refinement_inputs(graph, root, region_id),
            payload.inputId, payload.decision, payload.waitSeconds, payload.evidenceNote)
        audit = validate_editor_graph(draft)
        if file_revision(editor_paths(root, region_id)) != payload.revision:
            raise HTTPException(409, detail={'message': '审阅期间源文件已变化，请重新加载后应用'})
        return {'graph': draft, 'audit': audit, 'revision': payload.revision,
                'note': '核查选择已加入草案，仍标为人工审阅未实地核实；可撤销，保存后写入区域'}


@router.post('/refinement/plan')
async def refinement_plan(request: Request, payload: RefinementPlan, regionId: str | None = None):
    _local_access(request)
    try:
        return await run_in_threadpool(plan_editor_refinement, payload, REPO_ROOT, regionId)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


@router.post('/refinement/apply')
async def refinement_apply(request: Request, payload: RefinementDecision, regionId: str | None = None):
    _local_access(request)
    try:
        return await run_in_threadpool(apply_editor_refinement, payload, REPO_ROOT, regionId)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


@router.post('/context/preview')
async def preview_context(request: Request, payload: ContextSelection, regionId: str | None = None):
    _local_access(request)
    try:
        with _lock:
            region = editor_region(regionId)
            identity = region.manifest['id']
            current = read_editor(REPO_ROOT,identity)
            if current['revision'] != payload.revision:
                raise HTTPException(409,detail={'message':'区域已更新，请重新加载后生成轮廓'})
        result = await generate_context(region,payload.boundsMeters,REPO_ROOT/'data/cache')
        token = uuid4().hex
        record = {**result,'regionId':identity,'revision':payload.revision,'previewId':token,
                  'createdAt':datetime.now(timezone.utc).timestamp()}
        folder = REPO_ROOT/'data/cache/context-previews'
        folder.mkdir(parents=True,exist_ok=True)
        _replace(folder/(token+'.json'),_encoded(record))
        return result | {'previewId':token,'revision':payload.revision}
    except (OSError,ValueError,KeyError,TypeError,BaiduApiError) as exc:
        raise HTTPException(422,detail={'message':f'本地轮廓生成失败：{exc}'}) from exc


def save_context(payload: ContextSave, root: Path, region_id: str | None = None):
    with _lock:
        region = editor_region(region_id,root)
        identity = region.manifest['id']
        path = root/'data/cache/context-previews'/(payload.previewId+'.json')
        if not path.is_file():
            raise HTTPException(409,detail={'message':'轮廓预览已失效，请重新生成'})
        record = json.loads(path.read_text('utf-8'))
        if (record['regionId'] != identity or record['revision'] != payload.revision or
                datetime.now(timezone.utc).timestamp()-record['createdAt'] > 86400 or
                read_editor(root,identity)['revision'] != payload.revision):
            raise HTTPException(409,detail={'message':'区域已更新或预览已过期，请重新生成轮廓'})
        context = record['context']
        origin_key = 'originWgs84' if 'originWgs84' in region.graph() else 'originBd09'
        origin = region.graph()[origin_key]
        if context.get(origin_key) != [origin['lng'],origin['lat']] or not context.get('features'):
            raise ValueError('轮廓原点或几何数据无效')
        context_path, manifest_path = region.file('context'),region.root/'manifest.json'
        before = {p:p.read_bytes() for p in (context_path,manifest_path)}
        backup = root/'data/cache/network-editor-backups'/(
            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-context-'+uuid4().hex[:8])
        backup.mkdir(parents=True)
        for p,contents in before.items(): (backup/p.name).write_bytes(contents)
        if read_editor(root,identity)['revision'] != payload.revision:
            raise HTTPException(409,detail={'message':'保存期间区域发生变化，请重新生成轮廓'})
        try:
            _replace(context_path,_encoded(context))
            manifest = {**region.manifest,'contextSource':{
                'provider':context['source'],'license':context['sourceLicense'],
                'url':context['sourceUrl'],'generatedAt':context['generatedAt']}}
            _replace(manifest_path,_encoded(manifest,pretty=True))
            refresh_manifest(load_region(region.root,verify=False))
            load_region(region.root)
        except (OSError,ValueError,KeyError,TypeError):
            for p,contents in before.items(): _replace(p,contents)
            raise
        return {'context':context,'revision':read_editor(root,identity)['revision'],
                'featureCount':record['featureCount'],'counts':record['counts'],
                'backup':str(backup.relative_to(root))}


@router.put('/context')
async def put_context(request: Request, payload: ContextSave, regionId: str | None = None):
    _local_access(request)
    try:
        return await run_in_threadpool(save_context,payload,REPO_ROOT,regionId)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise HTTPException(422,detail={'message':f'本地轮廓保存失败：{exc}'}) from exc


@router.get('')
def get_network(request: Request, regionId: str | None = None) -> dict:
    _local_access(request)
    try:
        return read_editor(REPO_ROOT, regionId)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


@router.post('/validate')
async def validate_network(request: Request, payload: EditorGraph, regionId: str | None = None) -> dict:
    _local_access(request)
    try:
        return await check_candidate(payload, regionId)
    except EngineBusy:
        raise
    except EngineError as exc:
        raise HTTPException(422, detail={'message': f'C++ 检查未通过：{exc}'}) from exc
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise _bad_data(exc) from exc


async def check_candidate(payload: EditorGraph, region_id: str | None = None) -> dict:
    graph = await run_in_threadpool(candidate_graph, payload, REPO_ROOT, region_id)
    return await check_engine_graph(graph)


async def check_engine_graph(graph: dict) -> dict:
    audit = validate_editor_graph(graph)
    if not graph['edges'] and graph.get('authoringWorkspace'):
        return {'audit': audit, 'engine': '', 'status': 'draft'}
    edge = next(e for e in graph['edges'] if is_walkway(e))
    a, b = edge['pathMeters'][:2]
    result = await run_engine({**graph, 'originMeters': {
        'xMeters': (a[0] + b[0]) / 2, 'yMeters': (a[1] + b[1]) / 2},
        'originEdgeId': edge['id'], 'thresholdSeconds': 900,
        'walkingSpeedMetersPerSecond': 1.3, 'crossingWaitSeconds': 20,
        'facilitiesOnly': True})
    return {'audit': audit, 'engine': result.get('diagnostics', {}).get('buildMode', 'unknown')}


@router.put('')
async def put_network(request: Request, payload: EditorGraph, regionId: str | None = None) -> dict:
    _local_access(request)
    try:
        checked = await check_candidate(payload, regionId)
        result = await run_in_threadpool(save_editor, payload, REPO_ROOT, regionId)
        result['engine'] = checked['engine']
        return result
    except EngineBusy:
        raise
    except EngineError as exc:
        raise HTTPException(422, detail={'message': f'C++ 检查未通过，未保存：{exc}'}) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


@router.get('/regions')
def list_regions(request: Request) -> dict:
    _local_access(request)
    items = []
    root = REPO_ROOT / 'data/regions'
    for directory in sorted(root.iterdir()) if root.is_dir() else []:
        if not directory.is_dir() or directory.name.startswith('.'):
            continue
        try:
            region = editor_region(directory.name)
            metadata = region.public_metadata()
            items.append({key: metadata[key] for key in ('id', 'name', 'version', 'nodeCount',
                          'edgeCount', 'boundsMeters', 'synthetic', 'status')})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            items.append({'id': directory.name, 'name': directory.name, 'error': str(exc)})
    return {'items': items, 'defaultRegionId': settings.region_id}


@router.post('/regions', status_code=201)
def new_region(request: Request, payload: NewRegion):
    _local_access(request)
    try:
        with _lock:
            return create_blank_region(payload.location, payload.name, payload.centerBd09, REPO_ROOT)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail={'message': f'区域创建失败：{exc}'}) from exc


@router.get('/regions/{region_id}')
def region_metadata(request: Request, region_id: str) -> dict:
    _local_access(request)
    try:
        metadata = editor_region(region_id).public_metadata()
        metadata['assets'] = {key: url.replace('/api/v1/region/assets/',
            f'/api/v1/network-editor/regions/{region_id}/assets/') for key, url in metadata['assets'].items()}
        return metadata
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


@router.get('/regions/{region_id}/assets/{asset}')
def region_asset(request: Request, region_id: str, asset: str):
    _local_access(request)
    if asset not in ('context', 'alignment', 'pois'):
        raise HTTPException(404, detail='Unknown region asset')
    try:
        return Response(editor_region(region_id).file(asset).read_bytes(), media_type='application/json',
                        headers={'Cache-Control': 'no-cache'})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


def package_candidate(payload: PackageSelection, region_id: str | None = None):
    return crop_graph(candidate_graph(payload, REPO_ROOT, region_id), payload.boundsMeters)


@router.post('/package/preview')
async def preview_package(request: Request, payload: PackageSelection, regionId: str | None = None):
    _local_access(request)
    try:
        _, summary = await run_in_threadpool(package_candidate, payload, regionId)
        return summary
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


def publish_package(payload: PackageExport, region_id: str | None = None):
    with _lock:
        graph, summary = package_candidate(payload, region_id)
        return create_region(editor_region(region_id), graph, summary, payload.id,
                             payload.name, payload.version, REPO_ROOT)


@router.post('/package', status_code=201)
async def export_package(request: Request, payload: PackageExport, regionId: str | None = None):
    _local_access(request)
    try:
        graph, _ = await run_in_threadpool(package_candidate, payload, regionId)
        checked = await check_engine_graph(graph)
        result = await run_in_threadpool(publish_package, payload, regionId)
        return {**result, 'engine': checked['engine']}
    except FileExistsError as exc:
        raise HTTPException(409, detail={'message': str(exc)}) from exc
    except EngineBusy:
        raise
    except EngineError as exc:
        raise HTTPException(422, detail={'message': f'C++ 检查未通过，未生成区域包：{exc}'}) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc


@router.get('/regions/{region_id}/download')
def download_package(request: Request, region_id: str):
    _local_access(request)
    try:
        region = editor_region(region_id)
        name = f"{region.manifest['id']}-{region.manifest['version']}.zip"
        return Response(region_zip(region), media_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _bad_data(exc) from exc
