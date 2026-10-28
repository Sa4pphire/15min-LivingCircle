#pragma once

#include <vector>

#include "isochrone/types.hpp"
#include "isochrone/execution_control.hpp"

namespace isochrone::detail {

inline constexpr double kEndpointTolerance = 1e-6;
inline constexpr double kFacilityAccessTolerance = 1.0;
inline constexpr double kOriginSideAmbiguityMeters = 2.0;
inline constexpr double kEpsilon = 1e-8;

double distance(Point a, Point b);
bool finite(Point point);
double path_length(const std::vector<Point>& path, const ExecutionControl& control);
Point interpolate(Point a, Point b, double fraction);
Point point_at(const std::vector<Point>& path, double offset,
               const ExecutionControl& control);
std::vector<Point> slice(const std::vector<Point>& path, double from, double to,
                         const ExecutionControl& control);
double segment_distance(Point point, Point a, Point b);

}  // namespace isochrone::detail
