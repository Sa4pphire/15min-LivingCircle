from typing import Literal

from pydantic import BaseModel, Field


class CenterPoint(BaseModel):
    lng: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    coordType: Literal["bd09ll", "wgs84ll"] = "bd09ll"


class AnalysisRequest(BaseModel):
    center: CenterPoint
    originEdgeId: str | None = None
    minutes: Literal[15] = 15
    forceRefresh: bool = False
    includePois: bool = False
    refreshPois: bool = False


class LocalExperimentRequest(BaseModel):
    center: CenterPoint
    originEdgeId: str | None = None
    includePois: bool = False
    refreshPois: bool = False


class PoiSearchRequest(BaseModel):
    center: CenterPoint
    radiusMeters: int = Field(default=1500, ge=100, le=5000)
    categories: list[Literal["education", "healthcare", "shopping", "public_service", "dining"]] = Field(
        default_factory=lambda: ["education", "healthcare", "shopping", "public_service", "dining"],
        min_length=1, max_length=5)
    refresh: bool = False


class AnalysisAccepted(BaseModel):
    analysisId: str
    status: Literal["queued"]
    cached: bool = False


class Progress(BaseModel):
    stage: str
    percent: int = Field(ge=0, le=100)


class AnalysisState(BaseModel):
    analysisId: str
    status: str
    progress: Progress
    result: dict | None = None
    error: str | None = None


class EngineHealth(BaseModel):
    status: Literal["ok", "unavailable", "error"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    environment: str
    baiduServerAkConfigured: bool
    engine: EngineHealth
