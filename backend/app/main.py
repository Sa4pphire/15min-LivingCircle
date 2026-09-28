from pathlib import Path
import sqlite3
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .engine import EngineError, check_engine, run_engine
from .local_experiment import (build_local_experiment_result,
                               load_local_experiment_request)
from .network import UnsupportedAreaError, build_analysis_result, load_engine_request
from .pois import CATEGORIES, PoiService, add_poi_result, enrich_engine_pois
from .baidu.errors import BaiduApiError
from .schemas import (
    AnalysisAccepted,
    AnalysisRequest,
    AnalysisState,
    CenterPoint,
    HealthResponse,
    LocalExperimentRequest,
    PoiSearchRequest,
    Progress,
)
from .settings import settings


app = FastAPI(
    title="15-Minute Life Circle API",
    version="0.1.0",
    description="Competition demo API for walkability and facility coverage analysis.",
)

_analyses: dict[str, AnalysisState] = {}
_local_experiments: dict[str, AnalysisState] = {}


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
    try:
        if include_pois and center is not None:
            analysis.progress = Progress(stage="cached-baidu-pois", percent=15)
            await enrich_engine_pois(engine_input, network_meta, center, refresh=refresh_pois)
            analysis.progress = Progress(stage="walking-graph", percent=45)
        engine_result = await run_engine(engine_input)
        analysis.result = add_poi_result(build_analysis_result(engine_result, network_meta),
                                        engine_result, network_meta)
        analysis.status = "completed"
        analysis.progress = Progress(stage="completed", percent=100)
    except (EngineError, BaiduApiError, OSError, KeyError, TypeError, ValueError) as exc:
        analysis.status = "failed"
        analysis.error = str(exc)
        analysis.progress = Progress(stage="failed", percent=100)


async def _run_local_experiment(experiment_id: str, engine_input: dict,
                                network_meta: dict, center: CenterPoint | None = None,
                                include_pois: bool = False, refresh_pois: bool = False) -> None:
    experiment = _local_experiments[experiment_id]
    experiment.status = "running"
    experiment.progress = Progress(stage="local-walking-graph", percent=30)
    try:
        if include_pois and center is not None:
            experiment.progress = Progress(stage="cached-baidu-pois", percent=15)
            await enrich_engine_pois(engine_input, network_meta, center, refresh=refresh_pois)
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


@app.get("/api/v1/local-experiments/{experiment_id}", response_model=AnalysisState)
async def get_local_experiment(experiment_id: str) -> AnalysisState:
    experiment = _local_experiments.get(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="local experiment not found")
    return experiment


@app.get("/api/v1/analyses/{analysis_id}", response_model=AnalysisState)
async def get_analysis(analysis_id: str) -> AnalysisState:
    analysis = _analyses.get(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return analysis


static_dir = Path(settings.static_dir)
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
