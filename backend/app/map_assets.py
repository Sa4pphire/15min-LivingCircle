"""Prepare fixed BD-09 map assets offline, never during a page/API request."""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile

from .baidu.client import BaiduClient
from .baidu.errors import BaiduApiError


MAP_ORIGIN_WGS84 = [121.505, 31.333]
DISPLAY_PADDING_METERS = 1300
CONVERSION_BATCH_SIZE = 100
ALIGNMENT_STEP_METERS = 250


def valid_point(point) -> bool:
    return (isinstance(point, (list, tuple)) and len(point) == 2
            and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in point)
            and -180 <= point[0] <= 180 and -90 < point[1] < 90)


def map_asset_input(boundary: dict) -> dict:
    geometry = boundary.get("geometry", {})
    rings = geometry.get("coordinates", [])
    if (geometry.get("type") != "Polygon" or len(rings) != 1
            or len(rings[0]) < 4 or not all(valid_point(point) for point in rings[0])
            or rings[0][0] != rings[0][-1]
            or boundary.get("properties", {}).get("coordType") != "wgs84ll"):
        raise ValueError("边界必须是闭合的 WGS-84 单环 Polygon")
    return {"boundaryCoordinatesWgs84": rings[0],
            "originWgs84": MAP_ORIGIN_WGS84,
            "displayPaddingMeters": DISPLAY_PADDING_METERS,
            "alignmentStepMeters": ALIGNMENT_STEP_METERS}


def alignment_grid(inputs: dict) -> tuple[dict, list[list[float]]]:
    """A bounded east/south local grid; conversion never runs in the browser."""
    corners = display_corners_wgs84(inputs)
    lng, lat = inputs["originWgs84"]
    scale_x = 111320 * math.cos(math.radians(lat))
    step = inputs["alignmentStepMeters"]
    min_x = math.floor((corners[0][0] - lng) * scale_x / step) * step
    max_x = math.ceil((corners[1][0] - lng) * scale_x / step) * step
    min_y = math.floor((lat - corners[0][1]) * 111320 / step) * step
    max_y = math.ceil((lat - corners[2][1]) * 111320 / step) * step
    columns = round((max_x - min_x) / step) + 1
    rows = round((max_y - min_y) / step) + 1
    points = [[lng + (min_x + column * step) / scale_x,
               lat - (min_y + row * step) / 111320]
              for row in range(rows) for column in range(columns)]
    return {"kind": "bilinear_grid", "localAxis": "east-south",
            "stepMeters": step, "minLocalMeters": [min_x, min_y],
            "columns": columns, "rows": rows}, points


def display_corners_wgs84(inputs: dict) -> list[list[float]]:
    # Equivalent to mapGeometry.js expandedLocalBounds + localToWgs.
    ring = inputs["boundaryCoordinatesWgs84"]
    origin = inputs["originWgs84"]
    padding = inputs["displayPaddingMeters"]
    dx = padding / (111320 * math.cos(math.radians(origin[1])))
    dy = padding / 111320
    west = min(point[0] for point in ring) - dx
    east = max(point[0] for point in ring) + dx
    north = max(point[1] for point in ring) + dy
    south = min(point[1] for point in ring) - dy
    return [[west, north], [east, north], [east, south], [west, south]]


def valid_map_asset(asset: dict, inputs: dict) -> bool:
    if not isinstance(asset, dict):
        return False
    geometry = asset.get("boundary", {}).get("geometry", {})
    rings = geometry.get("coordinates", [])
    corners = asset.get("displayCornersBd09", [])
    grid, _ = alignment_grid(inputs)
    alignment = asset.get("alignment", {})
    return (asset.get("schemaVersion") == 1 and asset.get("coordType") == "bd09ll"
            and asset.get("input") == inputs and valid_point(asset.get("centerBd09"))
            and geometry.get("type") == "Polygon" and len(rings) == 1
            and len(rings[0]) == len(inputs["boundaryCoordinatesWgs84"])
            and all(valid_point(point) for point in rings[0]) and rings[0][0] == rings[0][-1]
            and len(corners) == 4 and all(valid_point(point) for point in corners)
            and valid_alignment(alignment, grid))


def valid_alignment(alignment: dict, grid: dict) -> bool:
    if not isinstance(alignment, dict) or not all(alignment.get(key) == value for key, value in grid.items()):
        return False
    points = alignment.get("pointsBd09")
    columns, rows = grid["columns"], grid["rows"]
    if not isinstance(points, list) or len(points) != columns * rows or not all(valid_point(point) for point in points):
        return False
    difference = lambda a, b: [a[0] - b[0], a[1] - b[1]]
    for row in range(rows - 1):
        for column in range(columns - 1):
            a, b = points[row * columns + column:row * columns + column + 2]
            c, d = points[(row + 1) * columns + column:(row + 1) * columns + column + 2]
            for du in (difference(b, a), difference(d, c)):
                for dv in (difference(c, a), difference(d, b)):
                    if du[0] * dv[1] - du[1] * dv[0] >= -1e-12:
                        return False
    return True


async def export_map_asset(source: Path, output: Path, client: BaiduClient,
                           *, refresh: bool = False) -> tuple[dict, bool]:
    boundary = json.loads(source.read_text(encoding="utf-8"))
    inputs = map_asset_input(boundary)
    if output.is_file() and not refresh:
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
            if valid_map_asset(existing, inputs):
                return existing, False
        except (ValueError, TypeError, AttributeError):
            pass

    ring = inputs["boundaryCoordinatesWgs84"]
    corners = display_corners_wgs84(inputs)
    # Deduplicate the closing vertex and shared anchors. At most 100 per call.
    unique = list(dict.fromkeys(tuple(point) for point in [*ring, MAP_ORIGIN_WGS84, *corners]))
    converted = []
    for offset in range(0, len(unique), CONVERSION_BATCH_SIZE):
        batch = unique[offset:offset + CONVERSION_BATCH_SIZE]
        result = await client.convert_coordinates(batch, "wgs84ll", refresh=refresh)
        if len(result) != len(batch) or not all(valid_point(point) for point in result):
            raise BaiduApiError("坐标转换结果不是有效的 BD-09 坐标")
        converted.extend(result)
    lookup = dict(zip(unique, converted))
    project = lambda point: list(lookup[tuple(point)])
    grid, grid_points = alignment_grid(inputs)
    # Keep boundary batches unchanged so existing official responses are reused.
    grid_converted = []
    for offset in range(0, len(grid_points), CONVERSION_BATCH_SIZE):
        batch = grid_points[offset:offset + CONVERSION_BATCH_SIZE]
        result = await client.convert_coordinates(batch, "wgs84ll", refresh=refresh)
        if len(result) != len(batch) or not all(valid_point(point) for point in result):
            raise BaiduApiError("路网校准网格不是有效的 BD-09 坐标")
        grid_converted.extend(list(point) for point in result)
    alignment = {**grid, "pointsBd09": grid_converted,
                 "approximate": True, "outsideGrid": "reject"}
    if not valid_alignment(alignment, grid):
        raise BaiduApiError("路网校准网格退化，保留上次有效地图数据")
    asset = {
        "schemaVersion": 1, "coordType": "bd09ll", "input": inputs,
        "centerBd09": project(MAP_ORIGIN_WGS84),
        "displayCornersBd09": [project(point) for point in corners],
        "alignment": alignment,
        "boundary": {"type": "Feature", "geometry": {
            "type": "Polygon", "coordinates": [[project(point) for point in ring]]},
            "properties": {**boundary["properties"], "coordType": "bd09ll"}},
        "conversion": {"provider": "Baidu geoconv/v2", "model": 2,
                       "documentationUrl": "https://lbsyun.baidu.com/docs/webapi?title=geoconv/guide/changeposition-base",
                       "generatedAt": datetime.now(timezone.utc).isoformat()},
    }
    # Publish only after every batch succeeded; preserve the last good artifact.
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=f".{output.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(asset, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return asset, True
