"""生成步行 RouteMatrix 所需的等时圈采样点。"""

from math import cos, radians, sin

EARTH_RADIUS_METERS = 6_378_137.0
DEFAULT_RADII_METERS = (300.0, 600.0, 900.0, 1200.0)
DIRECTION_ANGLES = tuple(range(0, 360, 30))


# 将局部平面米制偏移量转换为经纬度
def _offset_to_lng_lat(
    center: tuple[float, float],
    x_meters: float,
    y_meters: float,
) -> tuple[float, float]:
    center_lng, center_lat = center
    latitude_radians = radians(center_lat)

    latitude_delta = y_meters / EARTH_RADIUS_METERS
    longitude_delta = x_meters / (
        EARTH_RADIUS_METERS * cos(latitude_radians)
    )

    return (
        center_lng + longitude_delta * 180 / 3.141592653589793,
        center_lat + latitude_delta * 180 / 3.141592653589793,
    )


# 围绕中心点生成中心样本和 48 个环形采样点
def generate_sampling_points(
    center: tuple[float, float],
    radii_meters: tuple[float, ...] = DEFAULT_RADII_METERS,
) -> list[dict[str, float | int]]:
    samples: list[dict[str, float | int]] = []

    center_lng, center_lat = center
    samples.append(
        {
            "sampleIndex": 0,
            "angleDegrees": 0,
            "radiusMeters": 0.0,
            "lng": center_lng,
            "lat": center_lat,
            "xMeters": 0.0,
            "yMeters": 0.0,
        }
    )

    sample_index = 1

    for angle_degrees in DIRECTION_ANGLES:
        angle_radians = radians(angle_degrees)

        for radius_meters in radii_meters:
            x_meters = radius_meters * cos(angle_radians)
            y_meters = radius_meters * sin(angle_radians)
            lng, lat = _offset_to_lng_lat(
                center,
                x_meters,
                y_meters,
            )

            samples.append(
                {
                    "sampleIndex": sample_index,
                    "angleDegrees": angle_degrees,
                    "radiusMeters": radius_meters,
                    "lng": lng,
                    "lat": lat,
                    "xMeters": x_meters,
                    "yMeters": y_meters,
                }
            )
            sample_index += 1

    return samples

# 将中心样本和 48 个目的地点拆分成 RouteMatrix 所需参数
def build_route_matrix_inputs(
    samples: list[dict[str, float | int]],
) -> tuple[tuple[float, float], list[tuple[float, float]]]:
    if len(samples) < 2:
        raise ValueError("至少需要中心点和一个目的地点")

    center = samples[0]

    if center.get("sampleIndex") != 0:
        raise ValueError("第一个采样点必须是中心点")

    try:
        origin = (
            float(center["lng"]),
            float(center["lat"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("中心点缺少有效经纬度") from None

    destinations: list[tuple[float, float]] = []

    for sample in samples[1:]:
        try:
            destinations.append(
                (
                    float(sample["lng"]),
                    float(sample["lat"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("目的地点缺少有效经纬度") from None

    return origin, destinations

# 将百度返回的距离和耗时合并回对应采样点
def merge_route_results(
    samples: list[dict[str, float | int]],
    route_results: list[dict[str, float]],
) -> list[dict[str, float | int]]:
    expected_count = len(samples) - 1

    if expected_count < 1:
        raise ValueError("至少需要一个目的地点")

    if len(route_results) != expected_count:
        raise ValueError("RouteMatrix 结果数量与目的地点数量不一致")

    merged: list[dict[str, float | int]] = []

    for index, sample in enumerate(samples):
        enriched = dict(sample)

        if index == 0:
            enriched["distanceMeters"] = 0.0
            enriched["durationSeconds"] = 0.0
        else:
            result = route_results[index - 1]

            try:
                enriched["distanceMeters"] = float(
                    result["distanceMeters"]
                )
                enriched["durationSeconds"] = float(
                    result["durationSeconds"]
                )
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "RouteMatrix 结果缺少有效距离或耗时"
                ) from None

        merged.append(enriched)

    return merged