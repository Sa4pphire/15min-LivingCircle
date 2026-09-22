from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .engine import check_engine
from .schemas import (
    AnalysisAccepted,
    AnalysisRequest,
    AnalysisState,
    HealthResponse,
    Progress,
)
from .settings import settings


app = FastAPI(
    title="15-Minute Life Circle API",
    version="0.1.0",
    description="Competition demo API for walkability and facility coverage analysis.",
)

_analyses: dict[str, AnalysisState] = {}


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


@app.post("/api/v1/analyses", status_code=202, response_model=AnalysisAccepted)
async def create_analysis(request: AnalysisRequest) -> AnalysisAccepted:
    analysis_id = uuid4().hex
    _analyses[analysis_id] = AnalysisState(
        analysisId=analysis_id,
        status="queued",
        progress=Progress(stage="scaffold", percent=0),
        result={"request": request.model_dump()},
    )
    return AnalysisAccepted(analysisId=analysis_id, status="queued")


@app.get("/api/v1/analyses/{analysis_id}", response_model=AnalysisState)
async def get_analysis(analysis_id: str) -> AnalysisState:
    analysis = _analyses.get(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return analysis


static_dir = Path(settings.static_dir)
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
