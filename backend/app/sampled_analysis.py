"""百度采样加 Python 插值的近似等时圈流程。"""

from typing import Any

from .contour import extract_isoline
from .interpolation import interpolate_duration_grid
from .route_sampling import RouteMatrixClient, collect_route_samples
from .sampled_geometry import multipolygon_to_bd09


# 组合百度采样、IDW 插值、轮廓提取和坐标转换
async def build_sampled_isochrone(
    client: RouteMatrixClient,
    center: tuple[float, float],
    *,
    threshold_seconds: float = 900.0,
    grid_step_meters: float = 100.0,
) -> dict[str, Any]:
    samples = await collect_route_samples(
        client,
        center,
    )

    max_radius = max(
        float(sample["radiusMeters"])
        for sample in samples
    )

    bounds = (
        -max_radius,
        max_radius,
        -max_radius,
        max_radius,
    )

    grid = interpolate_duration_grid(
        samples,
        bounds,
        step_meters=grid_step_meters,
    )

    local_isochrone = extract_isoline(
        grid,
        threshold_seconds=threshold_seconds,
    )

    map_isochrone = multipolygon_to_bd09(
        local_isochrone,
        center,
    )

    return {
        "sourceMode": "baidu-sampled",
        "coordinateSystem": "bd09ll",
        "approximate": True,
        "thresholdSeconds": threshold_seconds,
        "gridStepMeters": grid_step_meters,
        "durationSamples": samples,
        "isochrone": map_isochrone,
        "isochroneMeters": local_isochrone,
    }