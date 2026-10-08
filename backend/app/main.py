from pathlib import Path
import asyncio
import math
import sqlite3
from time import perf_counter
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from .engine import EngineError, check_engine, run_engine
from .local_experiment import (build_local_experiment_result,
                               load_local_experiment_request)
from .network import UnsupportedAreaError, build_analysis_result, load_engine_request
from .pois import CATEGORIES, PoiService, add_poi_result, enrich_engine_pois
from .poi_routes import PoiRouteStore, RouteUnavailable
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


app = FastAPI(
    title="15-Minute Life Circle API",
    version="0.1.0",
    description="Competition demo API for walkability and facility coverage analysis.",
)

_analyses: dict[str, AnalysisState] = {}
_sampled_analyses: dict[str, AnalysisState] = {}
_local_experiments: dict[str, AnalysisState] = {}
_poi_routes = PoiRouteStore()
_sampled_task_semaphore = asyncio.Semaphore(1)


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
    return {
        "items": [
            {
                "id": "shanghai-new-jiangwan",
                "name": "上海市杨浦区新江湾城街道",
                "query": "上海市杨浦区新江湾城街道",
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

    def publish(result: dict, metadata: dict, geometry_revision: int = 1) -> None:
        before = perf_counter()
        report = add_poi_result(build_analysis_result(result, metadata), result, metadata)
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
                    raise EngineError("当前引擎不支持设施专用计算，请重新构建 Debug 引擎")
                engine_result = {**engine_result, "facilityTravelTimes": facility_result["facilityTravelTimes"]}
            reachable_length = sum(math.dist(a, b) for edge in engine_result["reachableEdges"]
                                   if edge.get("kind") in ("sidewalk", "shared_way")
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
                    "status": "unavailable", "error": str(exc)}}
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

        # One sampled report can issue a RouteMatrix request, POI pages and
        # walking-detail requests.  Serializing this external-work section
        # prevents repeated browser clicks from competing for the same Baidu
        # QPS/cache budget and turning every report into a transient failure.
        async with _sampled_task_semaphore:
            result = await build_sampled_result(
                client,
                center,
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
        experiment.progress = Progress(stage="failed", percent=100)


def _queue_analysis(request: AnalysisRequest, background_tasks: BackgroundTasks,
                    network_path: Path | None = None) -> AnalysisAccepted:
    started = perf_counter()
    try:
        engine_input, network_meta = load_engine_request(
            request.center, request.originEdgeId, network_path=network_path)
    except UnsupportedAreaError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNSUPPORTED_AREA", "message": str(exc)},
        ) from exc
    analysis_id = uuid4().hex
    _analyses[analysis_id] = AnalysisState(
        analysisId=analysis_id,
        status="queued",
        progress=Progress(stage="queued", percent=0),
        timingsMs={"networkLoad": round((perf_counter() - started) * 1000, 3)},
    )
    background_tasks.add_task(_run_analysis, analysis_id, engine_input, network_meta,
                              request.center, request.includePois, request.refreshPois)
    return AnalysisAccepted(analysisId=analysis_id, status="queued")


def _queue_local_experiment(request: LocalExperimentRequest,
                            background_tasks: BackgroundTasks,
                            network_path: Path | None = None) -> AnalysisAccepted:
    try:
        engine_input, network_meta = load_local_experiment_request(
            request.center, request.originEdgeId, network_path=network_path)
    except UnsupportedAreaError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNSUPPORTED_AREA", "message": str(exc)},
        ) from exc
    experiment_id = uuid4().hex
    _local_experiments[experiment_id] = AnalysisState(
        analysisId=experiment_id,
        status="queued",
        progress=Progress(stage="queued", percent=0),
    )
    background_tasks.add_task(
        _run_local_experiment, experiment_id, engine_input, network_meta,
        request.center, request.includePois, request.refreshPois)
    return AnalysisAccepted(analysisId=experiment_id, status="queued")


@app.post("/api/v1/analyses", status_code=202, response_model=AnalysisAccepted)
async def create_analysis(request: AnalysisRequest,
                          background_tasks: BackgroundTasks) -> AnalysisAccepted:
    return _queue_analysis(request, background_tasks)

@app.post(
    "/api/v1/sampled-analyses",
    status_code=202,
    response_model=AnalysisAccepted,
)
async def create_sampled_analysis(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
) -> AnalysisAccepted:
    analysis_id = uuid4().hex

    _sampled_analyses[analysis_id] = AnalysisState(
        analysisId=analysis_id,
        status="queued",
        progress=Progress(
            stage="queued",
            percent=0,
        ),
    )

    background_tasks.add_task(
        _run_sampled_task,
        analysis_id,
        request.center,
    )

    return AnalysisAccepted(
        analysisId=analysis_id,
        status="queued",
    )


@app.post("/api/v1/synthetic-analyses", status_code=202,
          response_model=AnalysisAccepted)
async def create_synthetic_analysis(request: AnalysisRequest,
                                    background_tasks: BackgroundTasks) -> AnalysisAccepted:
    """Run the clearly labelled synthetic fixture, independent of the real network setting."""
    return _queue_analysis(request, background_tasks, settings.synthetic_network_path)


@app.post("/api/v1/local-experiments", status_code=202,
          response_model=AnalysisAccepted)
async def create_local_experiment(
        request: LocalExperimentRequest,
        background_tasks: BackgroundTasks) -> AnalysisAccepted:
    """Use the existing synthetic graph by default, without inventing facility data."""
    return _queue_local_experiment(request, background_tasks)


@app.post("/api/v1/pois/search")
async def search_pois(request: PoiSearchRequest) -> dict:
    """Cached POI discovery only, without asserting entrances or walkability."""
    try:
        service = PoiService()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail={"code": "POI_CACHE_UNAVAILABLE",
                            "message": "POI 缓存不可用，未调用百度接口，请检查缓存目录权限。"}) from exc
    try:
        items, metadata = await service.search(request.center, request.radiusMeters,
                                               request.categories, refresh=request.refresh)
        return {"items": items, "metadata": metadata,
                "categoryLabels": {key: CATEGORIES[key]["label"] for key in request.categories}}
    finally:
        await service.client.aclose()


@app.post("/api/v1/analyses/{analysis_id}/poi-route")
async def route_analysis_poi(analysis_id: str, request: PoiRouteRequest) -> dict:
    try:
        return await _poi_routes.route(analysis_id, request.poiId)
    except RouteUnavailable as exc:
        raise HTTPException(status_code=409 if exc.code == "ROUTE_CONTEXT_EXPIRED" else 404,
                            detail={"code": exc.code, "message": str(exc)}) from exc
    except (EngineError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503,
                            detail={"code": "POI_ROUTE_ENGINE_ERROR", "message": str(exc)}) from exc


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
