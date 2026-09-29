"""从耗时网格中提取指定阈值的近似等时圈。"""

from collections import defaultdict
from typing import Any


# 在两个网格点之间计算阈值线的交点
def _interpolate_crossing(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    threshold: float,
) -> tuple[float, float]:
    x1, y1, value1 = first
    x2, y2, value2 = second

    if value1 == value2:
        ratio = 0.5
    else:
        ratio = (threshold - value1) / (value2 - value1)

    ratio = max(0.0, min(1.0, ratio))

    return (
        x1 + (x2 - x1) * ratio,
        y1 + (y2 - y1) * ratio,
    )


# 根据网格单元的四个角点生成阈值线段
def _cell_segments(
    corners: dict[str, tuple[float, float, float]],
    threshold: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    bottom_left = corners["bottom_left"]
    bottom_right = corners["bottom_right"]
    top_right = corners["top_right"]
    top_left = corners["top_left"]

    case = 0
    if bottom_left[2] <= threshold:
        case |= 1
    if bottom_right[2] <= threshold:
        case |= 2
    if top_right[2] <= threshold:
        case |= 4
    if top_left[2] <= threshold:
        case |= 8

    edges = {
        "bottom": _interpolate_crossing(
            bottom_left,
            bottom_right,
            threshold,
        ),
        "right": _interpolate_crossing(
            bottom_right,
            top_right,
            threshold,
        ),
        "top": _interpolate_crossing(
            top_left,
            top_right,
            threshold,
        ),
        "left": _interpolate_crossing(
            bottom_left,
            top_left,
            threshold,
        ),
    }

    cases = {
        0: [],
        1: [("left", "bottom")],
        2: [("bottom", "right")],
        3: [("left", "right")],
        4: [("right", "top")],
        5: [("left", "bottom"), ("right", "top")],
        6: [("bottom", "top")],
        7: [("left", "top")],
        8: [("top", "left")],
        9: [("bottom", "top")],
        10: [("bottom", "right"), ("top", "left")],
        11: [("right", "top")],
        12: [("left", "right")],
        13: [("bottom", "right")],
        14: [("left", "bottom")],
        15: [],
    }

    return [
        (edges[first], edges[second])
        for first, second in cases[case]
    ]


# 将离散线段连接成 GeoJSON MultiPolygon
def extract_isoline(
    grid: list[dict[str, float]],
    *,
    threshold_seconds: float = 900.0,
) -> dict[str, Any]:
    if not grid:
        raise ValueError("耗时网格不能为空")

    points: dict[tuple[float, float], float] = {}

    for item in grid:
        try:
            x = round(float(item["xMeters"]), 9)
            y = round(float(item["yMeters"]), 9)
            duration = float(item["durationSeconds"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("耗时网格包含无效字段") from None

        points[(x, y)] = duration

    xs = sorted({point[0] for point in points})
    ys = sorted({point[1] for point in points})

    if len(xs) < 2 or len(ys) < 2:
        raise ValueError("耗时网格至少需要两个方向的网格线")

    segments: list[
        tuple[tuple[float, float], tuple[float, float]]
    ] = []

    for row in range(len(ys) - 1):
        for column in range(len(xs) - 1):
            x = xs[column]
            next_x = xs[column + 1]
            y = ys[row]
            next_y = ys[row + 1]

            corners = {
                "bottom_left": (x, y, points[(x, y)]),
                "bottom_right": (next_x, y, points[(next_x, y)]),
                "top_right": (next_x, next_y, points[(next_x, next_y)]),
                "top_left": (x, next_y, points[(x, next_y)]),
            }

            segments.extend(
                _cell_segments(corners, threshold_seconds)
            )

    adjacency: dict[tuple[float, float], list[tuple[int, tuple[float, float]]]] = defaultdict(list)

    for index, (first, second) in enumerate(segments):
        first_key = (round(first[0], 6), round(first[1], 6))
        second_key = (round(second[0], 6), round(second[1], 6))
        adjacency[first_key].append((index, second))
        adjacency[second_key].append((index, first))

    used: set[int] = set()
    rings: list[list[list[float]]] = []

    for index, (first, second) in enumerate(segments):
        if index in used:
            continue

        used.add(index)
        ring = [list(first), list(second)]
        start_key = (round(first[0], 6), round(first[1], 6))
        current_key = (round(second[0], 6), round(second[1], 6))

        while current_key != start_key:
            next_segment = next(
                (
                    item
                    for item in adjacency[current_key]
                    if item[0] not in used
                ),
                None,
            )

            if next_segment is None:
                break

            next_index, next_point = next_segment
            used.add(next_index)
            ring.append(list(next_point))
            current_key = (
                round(next_point[0], 6),
                round(next_point[1], 6),
            )

        if current_key == start_key and len(ring) >= 4:
            rings.append(ring)

    return {
        "type": "MultiPolygon",
        "coordinates": [
            [ring]
            for ring in rings
        ],
    }