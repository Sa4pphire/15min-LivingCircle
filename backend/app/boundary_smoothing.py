"""One unconstrained display smoother in local metres.

The raw C++ surface remains the business geometry. Only finite closed-ring
construction is required; validity and area changes are reported, not enforced.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
from time import perf_counter

from shapely.errors import ShapelyError
from shapely.geometry import shape

ALGORITHM_VERSION = "unconstrained-boundary-v4"
_PASSES = 4
_NUMERICAL_MARGIN_METERS = 1e-8
_Point2 = tuple[float, float]


@dataclass
class _Ring:
    points: list[_Point2]
    arc: list[float]
    length: float


def _reference_ring(coordinates, step: float) -> _Ring:
    """Arc-length samples plus *all* original vertices, with no corner cutting."""
    vertices: list[_Point2] = []
    for coordinate in coordinates:
        point = (float(coordinate[0]), float(coordinate[1]))
        if not all(math.isfinite(value) for value in point):
            raise ValueError("nonfinite_source_ring")
        if not vertices or point != vertices[-1]:
            vertices.append(point)
    if len(vertices) > 1 and vertices[-1] == vertices[0]:
        vertices.pop()
    if len(vertices) < 3:
        raise ValueError("invalid_source_ring")
    lengths = [math.dist(a, b) for a, b in
               zip(vertices, vertices[1:] + vertices[:1])]
    perimeter = sum(lengths)
    if not math.isfinite(perimeter) or perimeter <= 0:
        raise ValueError("invalid_source_ring")
    spacing = perimeter / max(3, math.ceil(perimeter / step))
    points, arc = [], []
    distance, sample_index = 0.0, 1
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        segment_length = lengths[index]
        points.append(start)
        arc.append(distance)
        segment_end = distance + segment_length
        while sample_index * spacing < segment_end:
            sample_distance = sample_index * spacing
            if sample_distance > distance:
                fraction = (sample_distance - distance) / segment_length
                points.append((start[0] + (end[0] - start[0]) * fraction,
                               start[1] + (end[1] - start[1]) * fraction))
                arc.append(sample_distance)
            sample_index += 1
        distance = segment_end
    return _Ring(points, arc, perimeter)


def _smooth_ring(ring: _Ring, window_radius: float) -> list[_Point2]:
    """Smooth in metre-scale arc neighbourhoods, with no feature locks or clamps.

    Use a fixed reference-arc parametrization so the points still correspond to
    the source. The 17-tap triangular kernel operates across grid-scale noise,
    rather than moving only toward the nearest (sometimes very close) vertices.
    """
    radius = min(window_radius, ring.length / 6.0)
    stencils = []
    for position in ring.arc:
        stencil = []
        # With a 10 m input grid these taps are 2.5 m apart. Sampling only
        # every 10 m could repeatedly hit the same phase of grid-scale jaggies.
        for tap in range(-8, 9):
            distance = (position + tap * radius / 8.0) % ring.length
            index = bisect_right(ring.arc, distance) - 1
            after = (index + 1) % len(ring.points)
            end_distance = ring.arc[after] if after else ring.length
            fraction = (distance - ring.arc[index]) / (end_distance - ring.arc[index])
            stencil.append((index, after, fraction, (9 - abs(tap)) / 81.0))
        stencils.append(stencil)
    current = list(ring.points)
    for _ in range(_PASSES):
        following = []
        for stencil in stencils:
            x = y = 0.0
            for index, after, fraction, weight in stencil:
                start, end = current[index], current[after]
                x += (start[0] + (end[0] - start[0]) * fraction) * weight
                y += (start[1] + (end[1] - start[1]) * fraction) * weight
            following.append((x, y))
        current = following
    return current


def _area_metrics(raw, candidate) -> dict:
    gained = candidate.difference(raw).area
    lost = raw.difference(candidate).area
    difference = gained + lost
    return {
        "symmetricDifferenceAreaSquareMeters": difference,
        "symmetricDifferenceRatio": difference / raw.area if raw.area else 0.0,
        "gainedAreaSquareMeters": gained,
        "lostAreaSquareMeters": lost,
    }


def _zero_area_metrics() -> dict:
    return {"symmetricDifferenceAreaSquareMeters": 0.0,
            "symmetricDifferenceRatio": 0.0,
            "gainedAreaSquareMeters": 0.0, "lostAreaSquareMeters": 0.0}


def _geometry_metrics(raw_geometry: dict, candidate_geometry: dict) -> dict:
    """Validity and area are observations, never acceptance gates for this mode."""
    metrics = {key: None for key in _zero_area_metrics()}
    metrics.update(sourceGeometryValid=None, outputGeometryValid=None)
    try:
        raw, candidate = shape(raw_geometry), shape(candidate_geometry)
        metrics.update(sourceGeometryValid=bool(raw.is_valid),
                       outputGeometryValid=bool(candidate.is_valid))
        if raw.is_valid and candidate.is_valid:
            metrics.update(_area_metrics(raw, candidate))
    except (ShapelyError, TypeError, ValueError, IndexError):
        # Even invalid polygons can be inspected as finite closed SVG rings.
        # Unavailable area diagnostics must not suppress the experimental ink.
        pass
    return metrics


def build_smoothed_display_geometry(geometry: dict, *, grid_step_meters=10.0) -> dict:
    """Return one {geometry, metadata} result, with no profile selection."""
    started = perf_counter()
    grid_step = float(grid_step_meters)
    if not math.isfinite(grid_step) or grid_step <= 0:
        grid_step = 10.0
    radius = grid_step * 2.0
    metadata = {
        "algorithmVersion": ALGORITHM_VERSION, "mode": "unconstrained",
        "displayOnly": True, "experimental": True, "constraintsEnforced": False,
        "inputGridStepMeters": grid_step, "resampleStepMeters": grid_step / 4.0,
        "smoothingWindowRadiusMeters": radius, "smoothingPasses": _PASSES,
        "smoothingKernelTapCount": 17,
        "toleranceMeters": None, "maximumSymmetricDifferenceRatio": None,
        "reachabilityProtectionMethod": "disabled", "topologyPreserved": None,
        "boundaryDeviationUpperBoundMethod": "corresponding_segment_certificate",
        "boundaryDeviationUpperBoundMeters": 0.0,
        "status": "unchanged", "fallbackReason": None,
        "sourceGeometryValid": None, "outputGeometryValid": None,
        **{key: None for key in _zero_area_metrics()},
    }
    output = geometry
    try:
        if geometry.get("type") != "MultiPolygon":
            raise ValueError("unsupported_geometry")
        coordinates = []
        maximum_displacement = 0.0
        for polygon in geometry["coordinates"]:
            if not polygon:
                raise ValueError("empty_polygon_ring_list")
            output_rings = []
            for source in polygon:
                ring = _reference_ring(source, grid_step / 4.0)
                candidate = _smooth_ring(ring, radius)
                if not all(math.isfinite(value) for point in candidate for value in point):
                    raise ValueError("nonfinite_output")
                maximum_displacement = max(
                    maximum_displacement,
                    max(math.dist(a, b) for a, b in zip(ring.points, candidate)),
                )
                output_rings.append([[x, y] for x, y in candidate + candidate[:1]])
            coordinates.append(output_rings)
        output = {"type": "MultiPolygon", "coordinates": coordinates}
        # Source edges are only subdivided and output retains their point order.
        # Corresponding segment displacement is a convex combination of endpoint
        # displacements: a continuous two-way bound, measured but never limited.
        bound = maximum_displacement + _NUMERICAL_MARGIN_METERS if maximum_displacement else 0.0
        metadata.update(_geometry_metrics(geometry, output))
        metadata.update(status="smoothed" if maximum_displacement else "unchanged",
                        boundaryDeviationUpperBoundMeters=bound,
                        componentCount=len(coordinates),
                        holeCount=sum(len(polygon) - 1 for polygon in coordinates))
    except (KeyError, TypeError, ValueError, IndexError, OverflowError, ZeroDivisionError):
        output = geometry
        metadata.update(status="raw_fallback", fallbackReason="unusable_ring_coordinates")
    metadata["processingMilliseconds"] = (perf_counter() - started) * 1000.0
    return {"geometry": output, "metadata": metadata}
