from pathlib import Path
import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
import math
import sqlite3
import json
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .analysis_scheduler import AnalysisScheduler, ScheduledAnalysis
from .analysis_errors import analysis_error_code
from .agent_api import router as agent_router
from .agent_config import AgentConfig
from .agent_runtime import AgentError, AgentRuntime
from .agent_tools import ToolError
from .engine import (EngineBusy, EngineError, check_engine, engine_wait_scope,
                     get_engine_runtime, run_engine)
from .local_experiment import (build_local_experiment_result,
                               load_local_experiment_request)
from .network import UnsupportedAreaError, build_analysis_result, load_engine_request
from .pois import CATEGORIES, PoiService, add_poi_result, enrich_engine_pois
from .poi_routes import PoiRouteStore, RouteUnavailable
from .sampled_poi_routes import route_sampled_poi
from .baidu.client import BaiduClient
from .sampled_service import run_sampled_analysis as build_sampled_result
from .baidu.errors import BaiduApiError
from .schemas import (
    AnalysisAccepted,
    AnalysisRequest,
    AnalysisState,
    CenterPoint,
    HealthResponse,
    LocalExperimentRequest,
    PoiSearchRequest,
    PoiRouteRequest,
    Progress,
)
from .settings import settings
from .shutdown import shutdown_services
from .network_editor import router as network_editor_router
from .region_package import load_region, load_region_by_id, list_display_regions, validate_region_center

analysis_logger = logging.getLogger("livingcircle.analysis")
analysis_logger.setLevel(logging.INFO)
analysis_logger.propagate = False

if not analysis_logger.handlers:
    analysis_logger.addHandler(logging.StreamHandler())


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _sampled_task_semaphore
    # A contended semaphore belongs to its event loop, just like the scheduler.
    _sampled_task_semaphore = asyncio.Semaphore(1)
    engine_runtime = get_engine_runtime(restart=True)
    application.state.engine_runtime = engine_runtime
    scheduler = AnalysisScheduler(workers=2, pending_limit=8,
                                  forget_context=_poi_routes.forget)
    application.state.analysis_scheduler = scheduler
    agent_runtime = None
    try:
        await scheduler.start()
        agent_runtime = AgentRuntime(AgentConfig.from_env(), _resolve_agent_context, _route_agent_poi)
        application.state.agent_runtime = agent_runtime
        yield
    finally:
        # Stop all admissions together, before the first await. Agent cleanup
        # must not postpone the existing shared five-second drain deadline.
        scheduler.stop_accepting()
        engine_runtime.begin_drain()
        if agent_runtime is not None:
            agent_runtime.closed = True

        async def cleanup():
            operations = [shutdown_services(scheduler, engine_runtime)]
            if agent_runtime is not None:
                operations.append(agent_runtime.close())
            results = await asyncio.gather(*operations, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

        owner = asyncio.create_task(cleanup(), name='application-agent-shutdown')
        cancelled = False
        while not owner.done():
            try:
                await asyncio.shield(owner)
            except asyncio.CancelledError:
                if owner.cancelled():
                    raise
                cancelled = True
        owner.result()
        if cancelled:
            raise asyncio.CancelledError


app = FastAPI(
    title="15-Minute Life Circle API",
    version="0.1.0",
    description="Competition demo API for walkability and facility coverage analysis.",
    lifespan=lifespan,
)
app.include_router(network_editor_router)
app.include_router(agent_router)


@app.exception_handler(EngineBusy)
async def engine_busy_response(_request: Request, exc: EngineBusy) -> JSONResponse:
    return JSONResponse(status_code=503,
                        content={"detail": {"code": "ENGINE_BUSY", "message": str(exc)}},
                        headers={"Retry-After": "1"})


@app.get("/api/v1/region")
async def active_region() -> dict:
    try:
        return load_region().public_metadata()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, detail={"message": f"区域包无法加载：{exc}"}) from exc


@app.get("/api/v1/region/assets/{asset}")
async def region_asset(asset: str) -> FileResponse:
    if asset not in ("context", "alignment", "pois"):
        raise HTTPException(404, detail="Unknown region asset")
    try:
        region = load_region()
        return FileResponse(region.file(asset), media_type="application/json",
                            headers={"Cache-Control": "no-cache"})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, detail={"message": f"区域资源无法加载：{exc}"}) from exc


@app.get('/api/v1/regions')
async def display_regions() -> dict:
    return list_display_regions()


def _display_region(identity: str):
    try:
        return load_region_by_id(identity)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail={'code': 'REGION_NOT_FOUND', 'message': '区域包不存在，请刷新列表。'}) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail={'code': 'INVALID_REGION_PACKAGE', 'message': f'区域包无法加载：{exc}'}) from exc


@app.get('/api/v1/regions/{region_id}')
async def display_region_metadata(region_id: str) -> dict:
    try:
        return _display_region(region_id).public_metadata(asset_base=f'/api/v1/regions/{region_id}/assets')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail={'code': 'INVALID_REGION_PACKAGE', 'message': str(exc)}) from exc


@app.get('/api/v1/regions/{region_id}/assets/{asset}')
async def display_region_asset(region_id: str, asset: str) -> FileResponse:
    if asset not in ('context', 'alignment', 'pois'):
        raise HTTPException(404, detail='Unknown region asset')
    return FileResponse(_display_region(region_id).file(asset), media_type='application/json',
                        headers={'Cache-Control': 'no-cache'})


def _analysis_region(identity: str | None):
    if identity is None:
        return None
    region = _display_region(identity)
    if not region.graph()['edges']:
        raise HTTPException(422, detail={'code': 'REGION_NOT_READY', 'message': '该区域尚无路网，请先在编辑器中保存步行通道。'})
    return region

_analyses: dict[str, AnalysisState] = {}
_sampled_analyses: dict[str, AnalysisState] = {}
_local_experiments: dict[str, AnalysisState] = {}
_poi_routes = PoiRouteStore()
_sampled_task_semaphore = asyncio.Semaphore(1)


def _resolve_agent_context(context: dict) -> dict:
    """Resolve browser references against completed server-owned analysis data."""
    store = _sampled_analyses if context['mode'] == 'real' else _analyses
    state = store.get(context['analysisId'])
    if state is None:
        raise AgentError('ROUTE_CONTEXT_EXPIRED', '当前分析已过期，请重新计算生活圈后开始新对话。')
    if state.status != 'completed':
        raise AgentError('ANALYSIS_NOT_READY', '请先等待当前生活圈分析完成。')
    if not isinstance(state.result, dict):
        raise AgentError('ROUTE_CONTEXT_EXPIRED', '当前分析结果不可用，请重新计算生活圈。')
    report = state.result
    region_id = report.get('regionId') or report.get('metadata', {}).get('regionId') or settings.region_id
    actual_mode = ('real' if report.get('sourceMode') == 'baidu-sampled'
                   else 'synthetic' if report.get('metadata', {}).get('networkSource') == 'synthetic' else None)
    if (region_id != context['regionId'] or actual_mode != context['mode']
            or state.resultRevision != context['resultRevision']):
        raise AgentError('AGENT_CONTEXT_CHANGED', '区域、计算模式或分析版本已变化，请重新打开当前结果并开始新对话。')
    if context['mode'] == 'synthetic':
        route_context = _poi_routes.contexts.get(context['analysisId'])
        if route_context is None:
            raise AgentError('ROUTE_CONTEXT_EXPIRED', '当前分析的路线信息已过期，请重新计算生活圈。')
        origin = route_context[0].get('originMeters', {})
        # A private same-coordinate origin is only used for query ordering.
        return {**report, '_agentOriginMeters': [origin.get('xMeters'), origin.get('yMeters')]}
    return report


async def _route_agent_poi(context: dict, poi_id: str) -> dict:
    report = _resolve_agent_context(context)
    try:
        if context['mode'] == 'synthetic':
            return await _poi_routes.route(context['analysisId'], poi_id)
        client = BaiduClient(cache_enabled=True)
        try:
            return await route_sampled_poi(client, context['analysisId'], poi_id, report)
        finally:
            await client.aclose()
    except RouteUnavailable as exc:
        raise ToolError(exc.code, str(exc)) from exc
    except EngineBusy as exc:
        raise ToolError('ENGINE_BUSY', '路线计算服务较忙，当前设施未完成比较。') from exc
    except (EngineError, BaiduApiError, OSError, sqlite3.Error, TimeoutError, ValueError, KeyError, TypeError) as exc:
        raise ToolError('POI_ROUTE_UNAVAILABLE', '未能取得当前设施的有效路线；未据此判断它不存在或不可达。') from exc


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    engine = await check_engine()
    return HealthResponse(
        status="ok" if engine.status == "ok" else "degraded",
        environment=settings.app_env,
        baiduServerAkConfigured=bool(settings.baidu_server_ak),
        engine=engine,
    )


@app.get("/api/v1/presets")
async def presets() -> dict:
    region = load_region().public_metadata()
    return {
        "items": [
            {
                "id": region['id'],
                "name": region['name'],
                "query": region['name'],
                "center": None,
            }
        ]
    }


async def _run_analysis(analysis_id: str, engine_input: dict,
                        network_meta: dict, center: CenterPoint | None = None,
                        include_pois: bool = False, refresh_pois: bool = False) -> None:
    analysis = _analyses[analysis_id]
    analysis.status = "running"
    analysis.progress = Progress(stage="walking-graph", percent=30)
    started = perf_counter()

    def record_engine(prefix: str, result: dict, elapsed: float) -> None:
        analysis.timingsMs[prefix] = round(elapsed * 1000, 3)
        for name, value in result.get("diagnostics", {}).get("timingsMs", {}).items():
            analysis.timingsMs[f"{prefix}.{name}"] = round(value, 3)
        invocation = result.get("diagnostics", {}).get("invocation", {})
        if "permitWaitMs" in invocation:
            analysis.timingsMs[f"{prefix}.permitWait"] = invocation["permitWaitMs"]

    def publish(result: dict, metadata: dict, geometry_revision: int = 1) -> None:
        before = perf_counter()
        previous = analysis.result
        display_report = previous if (previous is not None and
            previous.get("metadata", {}).get("geometryRevision") == geometry_revision and
            previous.get("metadata", {}).get("boundarySmoothing", {}).get("mode") == "unconstrained" and
            "displayIsochrone" in previous) else None
        report = add_poi_result(build_analysis_result(
            result, metadata, display_report=display_report), result, metadata)
        if metadata.get('regionId'):
            report['metadata']['regionId'] = metadata['regionId']
        analysis.timingsMs["report"] = round((perf_counter() - before) * 1000, 3)
        report["metadata"].update({"timingsMs": dict(analysis.timingsMs),
                                  "engineBuildMode": result.get("diagnostics", {}).get("buildMode", "unknown"),
                                  "geometryRevision": geometry_revision})
        analysis.result = report
        analysis.resultRevision += 1

    try:
        before = perf_counter()
        engine_result = await run_engine(engine_input)
        record_engine("cpp", engine_result, perf_counter() - before)
        wants_pois = include_pois and center is not None
        pending_meta = {**network_meta, "poiRecords": [], "poi": {
            "status": "pending", "cacheOnly": not refresh_pois,
            "inventoryVerified": False, "accessVerified": False}} if wants_pois else network_meta
        publish(engine_result, pending_meta)
        analysis.timingsMs["firstResult"] = round((perf_counter() - started) * 1000, 3)
        analysis.result["metadata"]["timingsMs"] = dict(analysis.timingsMs)
        if not wants_pois:
            analysis.timingsMs["total"] = analysis.timingsMs["firstResult"]
            analysis.result["metadata"]["timingsMs"] = dict(analysis.timingsMs)
            analysis.status = "completed"
            analysis.progress = Progress(stage="completed", percent=100)
            _poi_routes.remember(analysis_id, engine_input, network_meta, analysis.result)
            return
    except (EngineError, BaiduApiError, OSError, KeyError, TypeError, ValueError) as exc:
        analysis.status = "failed"
        analysis.error = str(exc)
        analysis.errorCode = analysis_error_code(exc)
        analysis.progress = Progress(stage="failed", percent=100)
        analysis.timingsMs["total"] = round((perf_counter() - started) * 1000, 3)
        return

    # Publish geometry before doing any online/cache POI work.
    analysis.poiStatus = "pending"
    analysis.progress = Progress(stage="poi-refresh" if refresh_pois else "poi-cache", percent=70)
    await asyncio.sleep(0)
    original_facilities = engine_input.get("facilities", [])
    geometry_revision = 1
    try:
        before = perf_counter()
        await enrich_engine_pois(engine_input, network_meta, center, refresh=refresh_pois,
                                 engine_result=engine_result, cache_only=not refresh_pois)
        analysis.timingsMs["poi"] = round((perf_counter() - before) * 1000, 3)
        categories = network_meta.get("serviceCategories", [])
        incomplete_only = all(item["dataStatus"] == "incomplete" for item in categories)
        if incomplete_only:
            # Unverified online inventories cannot create confirmed gray zones.
            # Reuse the first surface and compute only new facility travel times.
            if engine_input.get("facilities", []) != original_facilities:
                analysis.progress = Progress(stage="facility-times", percent=90)
                before = perf_counter()
                facility_result = await run_engine({**engine_input, "facilitiesOnly": True})
                record_engine("facilityCpp", facility_result, perf_counter() - before)
                if facility_result.get("displayGeometryMeters", {}).get("coordinates"):
                    raise EngineError("当前引擎不支持设施专用计算，请重新构建 Debug 引擎",
                                      code="ENGINE_PROTOCOL_ERROR")
                engine_result = {**engine_result, "facilityTravelTimes": facility_result["facilityTravelTimes"]}
            reachable_length = sum(math.dist(a, b) for edge in engine_result["reachableEdges"]
                                   if edge.get("kind") in ("walkway", "sidewalk", "shared_way")
                                   for a, b in zip(edge["pathMeters"], edge["pathMeters"][1:]))
            engine_result = {**engine_result, "grayZones": [{
                "category": item["id"], "status": "data_insufficient", "uncoveredEdges": [],
                "displayGeometryMeters": {"type": "MultiPolygon", "coordinates": []},
                "reachableLengthMeters": reachable_length, "uncoveredLengthMeters": None,
                "uncoveredLengthRatio": None} for item in categories]}
        else:
            # Preserve full coverage semantics for any reviewed categories.
            before = perf_counter()
            engine_result = await run_engine(engine_input)
            record_engine("coverageCpp", engine_result, perf_counter() - before)
            geometry_revision = 2
        status = network_meta.get("poi", {}).get("status", "unavailable")
        analysis.poiStatus = status if status in ("ready", "partial", "unavailable") else "unavailable"
        publish(engine_result, network_meta, geometry_revision)
    except (EngineError, BaiduApiError, OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        analysis.poiStatus = "error"
        metadata = {**network_meta, "poi": {**network_meta.get("poi", {}),
                    "status": "unavailable", "error": str(exc),
                    "errorCode": analysis_error_code(exc)}}
        analysis.result = add_poi_result(analysis.result, engine_result, metadata)
        analysis.resultRevision += 1
    finally:
        analysis.timingsMs["total"] = round((perf_counter() - started) * 1000, 3)
        analysis.result["metadata"]["timingsMs"] = dict(analysis.timingsMs)
        analysis.result["metadata"]["poiStageStatus"] = analysis.poiStatus
        _poi_routes.remember(analysis_id, engine_input, network_meta, analysis.result)
        analysis.status = "completed"
        analysis.progress = Progress(stage="completed", percent=100)

# 后台执行百度采样、IDW 插值和等时圈提取
async def _run_sampled_task(
    analysis_id: str,
    center: CenterPoint,
    region_id: str | None = None,
) -> None:
    analysis = _sampled_analyses[analysis_id]
    analysis.status = "running"
    analysis.progress = Progress(
        stage="coordinate-normalization",
        percent=10,
    )

    client = BaiduClient()

    try:
        analysis.progress = Progress(
            stage="route-matrix",
            percent=30,
        )

        async with _sampled_task_semaphore:
            result = await build_sampled_result(
                client,
                center,
                **({'region_id': region_id} if region_id else {}),
            )

        analysis.result = result
        analysis.status = "completed"
        analysis.progress = Progress(
            stage="completed",
            percent=100,
        )
    except (
        BaiduApiError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        analysis.status = "failed"
        analysis.error = str(exc)
        analysis.errorCode = analysis_error_code(exc)
        analysis.progress = Progress(
            stage="failed",
            percent=100,
        )
    finally:
        await client.aclose()


async def _run_local_experiment(experiment_id: str, engine_input: dict,
                                network_meta: dict, center: CenterPoint | None = None,
                                include_pois: bool = False, refresh_pois: bool = False) -> None:
    experiment = _local_experiments[experiment_id]
    experiment.status = "running"
    experiment.progress = Progress(stage="local-walking-graph", percent=30)
    try:
        engine_result = await run_engine(engine_input)
        if include_pois and center is not None:
            experiment.progress = Progress(stage="cached-baidu-pois", percent=35)
            await enrich_engine_pois(engine_input, network_meta, center, refresh=refresh_pois,
                                     engine_result=engine_result)
            experiment.progress = Progress(stage="local-walking-graph", percent=45)
            engine_result = await run_engine(engine_input)
        experiment.result = add_poi_result(build_local_experiment_result(engine_result, network_meta),
                                          engine_result, network_meta)
        experiment.status = "completed"
        experiment.progress = Progress(stage="completed", percent=100)
    except (EngineError, BaiduApiError, OSError, KeyError, TypeError, ValueError) as exc:
        experiment.status = "failed"
        experiment.error = str(exc)
        experiment.errorCode = analysis_error_code(exc)
        experiment.progress = Progress(stage="failed", percent=100)


def _load_analysis_input(request: AnalysisRequest, network_path: Path | None):
    """Only file/input preparation runs in the thread pool, not admission/state."""
    region = _analysis_region(request.regionId)
    if region:
        network_path = region.file('network')
    engine_input, network_meta = load_engine_request(
        request.center, request.originEdgeId, network_path=network_path)
    if region:
        network_meta['regionId'] = region.manifest['id']
    return (engine_input, network_meta, request.center,
            request.includePois, request.refreshPois)


def _load_sampled_input(request: AnalysisRequest):
    region = _analysis_region(request.regionId)
    try:
        validate_region_center(request.center, region)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnsupportedAreaError(str(exc)) from exc
    return request.center, request.regionId


def _load_local_experiment_input(request: LocalExperimentRequest,
                                 network_path: Path | None):
    engine_input, network_meta = load_local_experiment_request(
        request.center, request.originEdgeId, network_path=network_path)
    return (engine_input, network_meta, request.center,
            request.includePois, request.refreshPois)


async def _prepare_analysis_input(prepare: Callable[[], tuple]):
    preparation = asyncio.create_task(asyncio.to_thread(prepare))
    try:
        return await asyncio.shield(preparation)
    except asyncio.CancelledError:
        # Cancelling to_thread's await does not stop its underlying thread.
        # Keep the pending reservation until the actual preparation has ended.
        while not preparation.done():
            try:
                await asyncio.shield(preparation)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not preparation.cancelled():
            preparation.exception()  # Retrieve any exception; cancellation stays authoritative.
        raise


def _analysis_busy() -> HTTPException:
    return HTTPException(503, detail={"code": "ANALYSIS_BUSY", "message": "分析任务已满或服务正在关闭，请稍后重试"},
                         headers={"Retry-After": "1"})


async def _run_scheduled_analysis(execute: Callable[..., Awaitable[None]],
                                   analysis_id: str, *prepared) -> None:
    # Keep the admitted identity across all stages, including shutdown drain.
    # Direct HTTP computations keep the adapter's default fast-fail policy.
    with engine_wait_scope(wait_timeout=5.0):
        await execute(analysis_id, *prepared)


async def _submit_analysis(store: dict[str, AnalysisState],
                           prepare: Callable[[], tuple],
                           execute: Callable[..., Awaitable[None]]) -> AnalysisAccepted:
    """Share admission/rollback while each analysis keeps its inputs and state store."""
    scheduler: AnalysisScheduler | None = getattr(app.state, "analysis_scheduler", None)
    reservation = scheduler.reserve() if scheduler is not None else None
    if reservation is None:
        analysis_logger.info(json.dumps({"event": "analysis.rejected", "reason": "busy"}))
        raise _analysis_busy()
    assert scheduler is not None

    started = perf_counter()
    analysis_id: str | None = None
    submitted = False
    try:
        try:
            prepared = await _prepare_analysis_input(prepare)
        except UnsupportedAreaError as exc:
            raise HTTPException(422, detail={"code": "UNSUPPORTED_AREA", "message": str(exc)}) from exc
        analysis_id = uuid4().hex
        analysis = AnalysisState(analysisId=analysis_id, status="queued",
                                 progress=Progress(stage="queued", percent=0),
                                 timingsMs={"networkLoad": round((perf_counter() - started) * 1000, 3)})
        store[analysis_id] = analysis
        job = ScheduledAnalysis(analysis, partial(_run_scheduled_analysis, execute,
                                                 analysis_id, *prepared), store=store)
        submitted = scheduler.submit(reservation, job)
        if not submitted:
            raise _analysis_busy()
        analysis_logger.info(json.dumps({"event": "analysis.accepted", "analysisId": analysis_id,
                                         "status": "queued", "networkLoadMs": analysis.timingsMs["networkLoad"]},
                                        ensure_ascii=False))
        return AnalysisAccepted(analysisId=analysis_id, status="queued")
    finally:
        if not submitted:
            if analysis_id is not None:
                store.pop(analysis_id, None)
            scheduler.release(reservation)


async def _queue_analysis(request: AnalysisRequest,
                          network_path: Path | None = None) -> AnalysisAccepted:
    return await _submit_analysis(
        _analyses, partial(_load_analysis_input, request, network_path), _run_analysis)


async def _queue_local_experiment(request: LocalExperimentRequest,
                                  network_path: Path | None = None) -> AnalysisAccepted:
    return await _submit_analysis(
        _local_experiments, partial(_load_local_experiment_input, request, network_path),
        _run_local_experiment)


@app.post("/api/v1/analyses", status_code=202, response_model=AnalysisAccepted)
async def create_analysis(request: AnalysisRequest) -> AnalysisAccepted:
    return await _queue_analysis(request)

@app.post(
    "/api/v1/sampled-analyses",
    status_code=202,
    response_model=AnalysisAccepted,
)
async def create_sampled_analysis(request: AnalysisRequest) -> AnalysisAccepted:
    return await _submit_analysis(
        _sampled_analyses, partial(_load_sampled_input, request), _run_sampled_task)


@app.post("/api/v1/synthetic-analyses", status_code=202,
          response_model=AnalysisAccepted)
async def create_synthetic_analysis(request: AnalysisRequest) -> AnalysisAccepted:
    """Run the clearly labelled synthetic fixture, independent of the real network setting."""
    return await _queue_analysis(request, settings.synthetic_network_path)


@app.post("/api/v1/local-experiments", status_code=202,
          response_model=AnalysisAccepted)
async def create_local_experiment(request: LocalExperimentRequest) -> AnalysisAccepted:
    """Use the existing synthetic graph by default, without inventing facility data."""
    return await _queue_local_experiment(request)


@app.post("/api/v1/pois/search")
async def search_pois(request: PoiSearchRequest) -> dict:
    """Cached POI discovery only, without asserting entrances or walkability."""
    try:
        region = _display_region(request.regionId) if request.regionId else load_region()
        service = PoiService(region=region)
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail={"code": "POI_CACHE_UNAVAILABLE",
                            "message": "POI 缓存不可用，未调用百度接口，请检查缓存目录权限。"}) from exc
    try:
        items, metadata = await service.search(request.center, request.radiusMeters,
                                               request.categories, refresh=request.refresh)
        return {"items": items, "metadata": metadata,
                "categoryLabels": {key: CATEGORIES[key]["label"] for key in request.categories}}
    except ValueError as exc:
        raise HTTPException(422, detail={'code': 'INVALID_POI_QUERY', 'message': str(exc)}) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, detail={'code': 'POI_CACHE_UNAVAILABLE',
            'message': '地点数据暂时无法读取，请检查区域包和缓存目录。'}) from exc
    finally:
        await service.client.aclose()


@app.post("/api/v1/analyses/{analysis_id}/poi-route")
async def route_analysis_poi(analysis_id: str, request: PoiRouteRequest) -> dict:
    try:
        return await _poi_routes.route(analysis_id, request.poiId)
    except RouteUnavailable as exc:
        raise HTTPException(status_code=409 if exc.code == "ROUTE_CONTEXT_EXPIRED" else 404,
                            detail={"code": exc.code, "message": str(exc)}) from exc
    except EngineBusy:
        raise
    except (EngineError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503,
                            detail={"code": "POI_ROUTE_ENGINE_ERROR", "message": str(exc)}) from exc


@app.post('/api/v1/sampled-analyses/{analysis_id}/poi-route')
async def route_sampled_analysis_poi(analysis_id: str, request: PoiRouteRequest) -> dict:
    state = _sampled_analyses.get(analysis_id)
    if state is None:
        raise HTTPException(409, detail={'code': 'ROUTE_CONTEXT_EXPIRED',
                                        'message': '该分析不存在或已过期，请重新计算真实区域分析。'})
    if state.status != 'completed':
        raise HTTPException(409, detail={'code': 'ANALYSIS_NOT_READY', 'message': '请等待真实区域分析完成后再规划路线。'})
    if state.result is None:
        raise HTTPException(409, detail={'code': 'ROUTE_CONTEXT_EXPIRED', 'message': '当前分析结果不可用，请重新计算。'})
    client = BaiduClient(cache_enabled=True)
    try:
        return await route_sampled_poi(client, analysis_id, request.poiId, state.result)
    except RouteUnavailable as exc:
        raise HTTPException(409 if exc.code == 'ROUTE_CONTEXT_EXPIRED' else 404,
                            detail={'code': exc.code, 'message': str(exc)}) from exc
    except (BaiduApiError, OSError, sqlite3.Error, TimeoutError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, detail={'code': 'BAIDU_ROUTE_UNAVAILABLE', 'message': str(exc)}) from exc
    finally:
        await client.aclose()


@app.get("/api/v1/local-experiments/{experiment_id}", response_model=AnalysisState)
async def get_local_experiment(experiment_id: str) -> AnalysisState:
    experiment = _local_experiments.get(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="local experiment not found")
    return experiment


@app.get("/api/v1/analyses/{analysis_id}", response_model=AnalysisState)
async def get_analysis(analysis_id: str, afterRevision: int | None = Query(default=None, ge=0)) -> AnalysisState:
    analysis = _analyses.get(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    if afterRevision is not None and analysis.resultRevision <= afterRevision:
        # Polling stage changes must not repeatedly encode the full map/POIs.
        return analysis.model_copy(update={"result": None})
    return analysis

@app.get(
    "/api/v1/sampled-analyses/{analysis_id}",
    response_model=AnalysisState,
)
async def get_sampled_analysis(
    analysis_id: str,
) -> AnalysisState:
    analysis = _sampled_analyses.get(analysis_id)

    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail="sampled analysis not found",
        )

    return analysis


static_dir = Path(settings.static_dir)
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
