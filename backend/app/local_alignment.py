"""Use the frontend's prepared BD-09 mesh for backend POI containment too."""

import json
import math

from .map_assets import valid_alignment, valid_point


def load_grid_frame(metadata, path):
    if metadata["coordType"] != "wgs84ll" or not path.is_file():
        return None
    asset = json.loads(path.read_text(encoding="utf-8"))
    origin = metadata["originWgs84"]
    if asset.get("input", {}).get("originWgs84") != [origin["lng"], origin["lat"]]:
        return None
    grid = asset.get("alignment", {})
    columns, rows = grid.get("columns"), grid.get("rows")
    step, minimum = grid.get("stepMeters"), grid.get("minLocalMeters")
    if (asset.get("schemaVersion") != 1 or asset.get("coordType") != "bd09ll" or
            grid.get("kind") != "bilinear_grid" or grid.get("localAxis") != "east-south" or
            type(columns) is not int or type(rows) is not int or not 2 <= columns <= 128 or
            not 2 <= rows <= 128 or not isinstance(step, (int, float)) or isinstance(step, bool) or
            not math.isfinite(step) or not 0 < step <= 250 or not isinstance(minimum, list) or
            len(minimum) != 2 or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                      and math.isfinite(x) for x in minimum)):
        raise ValueError("POI 地图校准网格无效")
    expected = {key: grid[key] for key in ("kind", "localAxis", "columns", "rows", "stepMeters", "minLocalMeters")}
    if not valid_alignment(grid, expected):
        raise ValueError("POI 地图校准网格退化")
    points = grid["pointsBd09"]
    boxes = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            corners = [points[row * columns + column], points[row * columns + column + 1],
                       points[(row + 1) * columns + column], points[(row + 1) * columns + column + 1]]
            boxes.append((column, row, corners,
                          min(p[0] for p in corners), max(p[0] for p in corners),
                          min(p[1] for p in corners), max(p[1] for p in corners)))

    def cell(corners, u, v):
        a, b, c, d = corners
        position = [(1 - v) * ((1 - u) * a[i] + u * b[i]) +
                    v * ((1 - u) * c[i] + u * d[i]) for i in (0, 1)]
        du = [(1 - v) * (b[i] - a[i]) + v * (d[i] - c[i]) for i in (0, 1)]
        dv = [(1 - u) * (c[i] - a[i]) + u * (d[i] - b[i]) for i in (0, 1)]
        return position, du, dv, du[0] * dv[1] - du[1] * dv[0]

    def project(point):
        if not valid_point(point):
            return None
        for column, row, corners, west, east, south, north in boxes:
            if not west - 1e-10 <= point[0] <= east + 1e-10 or not south - 1e-10 <= point[1] <= north + 1e-10:
                continue
            u, v = 0.5, 0.5
            for _ in range(10):
                position, du, dv, determinant = cell(corners, u, v)
                dx, dy = position[0] - point[0], position[1] - point[1]
                if max(abs(dx), abs(dy)) < 1e-11:
                    break
                u -= (dx * dv[1] - dy * dv[0]) / determinant
                v -= (du[0] * dy - du[1] * dx) / determinant
            position = cell(corners, u, v)[0]
            if (-1e-7 <= u <= 1 + 1e-7 and -1e-7 <= v <= 1 + 1e-7 and
                    math.dist(position, point) < 1e-10):
                # C++ uses north-positive meters, unlike the frontend SVG axis.
                return [minimum[0] + (column + max(0, min(1, u))) * step,
                        -(minimum[1] + (row + max(0, min(1, v))) * step)]
        return None  # Do not extrapolate or accidentally treat BD-09 as WGS-84.

    return project, "prepared_baidu_bilinear_grid"
