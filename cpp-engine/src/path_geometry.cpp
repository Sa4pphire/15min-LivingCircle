#include "detail/path_geometry.hpp"

#include <algorithm>
#include <cmath>

namespace isochrone::detail {

double distance(Point a, Point b) { return std::hypot(a.x - b.x, a.y - b.y); }

bool finite(Point point) {
  return std::isfinite(point.x) && std::isfinite(point.y);
}

double path_length(const std::vector<Point>& path,
                   const ExecutionControl& control) {
  double result = 0.0;
  for (std::size_t i = 1; i < path.size(); ++i) {
    control.Poll();
    result += distance(path[i - 1], path[i]);
  }
  return result;
}

Point interpolate(Point a, Point b, double fraction) {
  return {a.x + (b.x - a.x) * fraction,
          a.y + (b.y - a.y) * fraction};
}

Point point_at(const std::vector<Point>& path, double offset,
               const ExecutionControl& control) {
  double walked = 0.0;
  for (std::size_t i = 1; i < path.size(); ++i) {
    control.Poll();
    const double length = distance(path[i - 1], path[i]);
    if (walked + length >= offset - kEpsilon) {
      return interpolate(path[i - 1], path[i],
                         length > 0.0 ? std::clamp((offset - walked) / length,
                                                   0.0, 1.0)
                                      : 0.0);
    }
    walked += length;
  }
  return path.back();
}

std::vector<Point> slice(const std::vector<Point>& path, double from,
                         double to, const ExecutionControl& control) {
  const double length = path_length(path, control);
  from = std::clamp(from, 0.0, length);
  to = std::clamp(to, from, length);
  std::vector<Point> result{point_at(path, from, control)};
  double walked = 0.0;
  for (std::size_t i = 1; i + 1 < path.size(); ++i) {
    control.Poll();
    walked += distance(path[i - 1], path[i]);
    if (walked > from + kEpsilon && walked < to - kEpsilon) {
      result.push_back(path[i]);
    }
  }
  const Point end = point_at(path, to, control);
  if (distance(result.back(), end) > kEpsilon) result.push_back(end);
  return result;
}

double segment_distance(Point point, Point a, Point b) {
  const double dx = b.x - a.x;
  const double dy = b.y - a.y;
  const double length_squared = dx * dx + dy * dy;
  const double fraction = length_squared > 0.0
                              ? std::clamp(((point.x - a.x) * dx +
                                            (point.y - a.y) * dy) /
                                               length_squared,
                                           0.0, 1.0)
                              : 0.0;
  return distance(point, interpolate(a, b, fraction));
}

}  // namespace isochrone::detail
